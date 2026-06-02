import json
import os
from datetime import date
from flask import Flask, jsonify

app = Flask(__name__)
BASE_DIR = os.path.join(os.path.dirname(__file__), "News_Headlines")

def load_date(date_str):
    year = date_str[:4]
    path = os.path.join(BASE_DIR, year, f"{date_str}.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)

@app.route('/')
def main():
    return 'use /api/today or /api/<date_str> to get a json'
    
@app.route("/api/today")
def today():
    return get_date(date.today().isoformat())

@app.route("/api/<date_str>")
def get_date(date_str):
    data = load_date(date_str)
    if data is None:
        return jsonify({"error": "No headlines found for this given date"}), 404
    data.pop("_meta", None)
    return jsonify(data)

