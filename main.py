import os
import time
import threading
import requests
from flask import Flask, Response
from huawei_lte_api.Connection import Connection
from huawei_lte_api.Client import Client
import urllib3

app = Flask(__name__)

# Configuration (override via environment variables)
ROUTER_URL = os.getenv("HUAWEI_ROUTER_URL", "https://192.168.8.1")
ROUTER_USER = os.getenv("HUAWEI_ROUTER_USER", "admin")
ROUTER_PASS = os.getenv("HUAWEI_ROUTER_PASS", "admin")
LISTEN_PORT = os.getenv("LISTEN_PORT", 8081)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Thread-safe in-memory cache
cache_lock = threading.Lock()
cache = {
    "data": None,
    "error": None,
    "last_updated": None
}


def format_signal_response(data: dict) -> str:
    if not data:
        return "Signal data not yet available."

    def get_val(*keys):
        for k in keys:
            if k in data:
                return str(data[k])
        return "N/A"

    header = """
    <!DOCTYPE html>
<html>
<head>
<meta http-equiv="refresh" content="1" />
<title>Signal Statistics</title>
<body>
<pre>
[placeholder]
</pre>
</body>
</html>
    """

    # Fixed-width labels to align exactly like the screenshot
    label_width = 26
    lines = [
        f"{f'CQI':<{label_width}} CQI0:{get_val('cqi0')} CQI1:{get_val('cqi1')}",
        f"{f'MIMO rank':<{label_width}} {get_val('rrc_status')}",
        f"{f'Wireless transmit power':<{label_width}} {get_val('txpower')}",
        f"{f'Uplink mod/demod of MCS':<{label_width}} {get_val('ul_mcs')}",
        f"{f'Downlink mod/demod of MCS':<{label_width}} {get_val('dl_mcs')}",
        f"{f'EARFCN':<{label_width}} {get_val('earfcn')}",
        f"{f'Cell ID':<{label_width}} {get_val('cell_id')}",
        f"{f'eNodeB ID':<{label_width}} {get_val('enodeb_id')}",
        f"{f'PCI':<{label_width}} {get_val('pci')}",
        f"{f'Band':<{label_width}} {get_val('band')}",
        f"{f'Signal':<{label_width}} RSSI: {get_val('rssi')}; RSRP: {get_val('rsrp')}; RSRQ: {get_val('rsrq')}; SINR: {get_val('sinr')}"
    ]
    return header.replace("[placeholder]", "\n".join(lines))


def background_refresh_loop():
    """
    Fully independent background loop.
    - Refreshes exactly every 1 second.
    - Never blocks or is triggered by HTTP requests.
    - Catches all errors (including timeouts) and stores them as strings in cache.
    """
    # Initialize connection once
    while True:
        try:
            session = requests.Session()
            session.verify = False
            session.trust_env = False
            conn = Connection(ROUTER_URL, username=ROUTER_USER, password=ROUTER_PASS, requests_session=session)
            client = Client(conn)
            break
        except Exception as e:
            with cache_lock:
                cache["error"] = f"Failed to initialize router connection: {str(e)}"
            session.close()
            time.sleep(1.0)

    while True:
        try:
            # Fetch signal data
            data = client.device.signal()

            with cache_lock:
                cache["data"] = data
                cache["error"] = None
                cache["last_updated"] = time.time()
        except Exception as e:
            # Guard against timeout / network / auth errors
            # Log exception as string in cache, keep previous data if available
            with cache_lock:
                cache["error"] = str(e)

        # Strict 1-second interval. Independent of request volume or timing.
        time.sleep(1.0)


@app.route("/")
def signal_endpoint():
    # CRITICAL: This route NEVER calls the router.
    # It ALWAYS returns cached data, regardless of age or request frequency.
    with cache_lock:
        data = cache["data"]
        error = cache["error"]

    # If we have an error but no data yet, return 502
    if error and not data:
        return Response(
            f"Error: {error}\nWaiting for background refresh...\n",
            status=502,
            mimetype="text/plain"
        )

    # Format cached data
    response_text = format_signal_response(data)

    # If background loop encountered an error, append it to response for visibility
    if error:
        return Response(
            f"{response_text}\n\n[Background Error: {error}]\n",
            status=200,
            mimetype="text/plain"
        )

    return Response(f"{response_text}\n", mimetype="text/html")


if __name__ == "__main__":
    # Start background refresh as a daemon thread
    refresh_thread = threading.Thread(target=background_refresh_loop, daemon=True)
    refresh_thread.start()

    # Wait briefly for first cache population (non-blocking for server start)
    print("Initializing signal cache...")
    start = time.time()
    while not cache["data"] and not cache["error"] and (time.time() - start) < 5:
        time.sleep(0.1)

    if cache["error"]:
        print(f"Warning: Initial fetch failed: {cache['error']}")
    else:
        print("Cache initialized successfully.")

    # Default: listen on localhost:8081
    print("Starting microservice on http://localhost:8081")
    app.run(host="127.0.0.1", port=int(LISTEN_PORT), debug=False, use_reloader=False)
