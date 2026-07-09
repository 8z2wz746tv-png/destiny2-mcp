"""Service layer — business logic for Destiny MCP tools.

Services depend on clients (BungieClient) and repositories (ManifestManager).
They contain the "how" — how to search, transfer, parse inventory — but are
invoked by the thin tool layer in server.py.
"""
