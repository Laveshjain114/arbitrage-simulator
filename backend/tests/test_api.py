import time

from fastapi.testclient import TestClient

from app.main import app


def test_api_and_websocket_end_to_end(tmp_path, monkeypatch):
    with TestClient(app) as client:
        assert client.get("/api/health").json() == {"ok": True}
        time.sleep(2.5)                       # let the simulator connect and stream
        status = client.get("/api/status").json()
        assert status["market_data"] == "STREAMING" and status["mode"] == "SIMULATION"
        assert {f["broker"] for f in status["feeds"]} == {"angelone", "zerodha"}

        quotes = client.get("/api/quotes").json()
        assert quotes[0]["symbol"] == "RELIANCE" and quotes[0]["angelone_NSE"] is not None

        r = client.put("/api/risk", json={"max_quantity": 250})
        assert r.json()["max_quantity"] == 250
        assert client.put("/api/risk", json={"max_quantity": 0}).status_code == 422

        assert client.post("/api/engine/stop").json()["engine"] == "STOPPED"
        assert client.post("/api/engine/start").json()["engine"] == "RUNNING"

        with client.websocket_connect("/ws") as ws:
            snap = ws.receive_json()
            assert snap["type"] == "snapshot"
            for key in ("status", "quotes", "opportunities", "trades", "orders", "portfolio", "analytics", "risk"):
                assert key in snap

        accounts = client.get("/api/accounts").json()
        assert {a["broker"] for a in accounts} == {"angelone", "zerodha"}
        assert all(a["account_type"] == "SIMULATED" and a["opening_balance"] > 0 for a in accounts)
        assert isinstance(client.get("/api/orders").json(), list)
        assert isinstance(client.get("/api/orders/history").json(), list)
