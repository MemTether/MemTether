"""How to use the MemTether REST API.

Start the API server:
  pip install "memtether[server]"
  uvicorn api_server:app --host 0.0.0.0 --port 8080

Then use any HTTP client:
"""
import json

API_BASE = "http://127.0.0.1:8080"

# Search
print("Search:")
print(f'  curl -X POST {API_BASE}/search -H "Content-Type: application/json" '
      f'-d '{{"query": "theme preference", "limit": 5}}'')

# List
print("\nList:")
print(f'  curl {API_BASE}/list?limit=10')

# Stats
print("\nStats:")
print(f'  curl {API_BASE}/stats')

# Docker
print("\nDocker:")
print("  docker-compose up")
print(f"  # API available at {API_BASE}")

print("\nAny language can use the REST API:")
print("  Node.js: fetch / axios")
print("  Go: net/http")
print("  Rust: reqwest")
print("  Java: HttpClient")
print("  Python: requests / urllib")
