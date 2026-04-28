from __future__ import annotations

from flask import Flask, jsonify, render_template, request

from scraper.store import Store


def create_app(store: Store) -> Flask:
    app = Flask(__name__)

    @app.get("/")
    def index():
        return render_template("index.html", query=store.query)

    @app.get("/api/cells")
    def api_cells():
        return jsonify(store.cells_geojson())

    @app.get("/api/places")
    def api_places():
        try:
            since = int(request.args.get("since", "0"))
        except ValueError:
            since = 0
        items, max_id = store.places_since(since)
        summary = store.cells_summary()
        return jsonify({
            "items": items,
            "max_id": max_id,
            "summary": summary,
            "query": store.query,
        })

    return app
