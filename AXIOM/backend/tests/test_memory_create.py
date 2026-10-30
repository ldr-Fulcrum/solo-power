from test_backend import AUTH, PERSONAL, post


def test_explicit_memory_opt_in_and_replay_survive_delete(client):
    assert (
        post(client, "/memory", {"value": "Synthetic preference"}, headers=PERSONAL).status_code
        == 422
    )
    created = post(
        client,
        "/memory",
        {"value": "Synthetic preference", "remember": True},
        headers=PERSONAL,
        key="opt-in",
    )
    assert created.status_code == 201 and created.json()["saved"] is True
    assert "value" not in created.json()
    assert client.get("/api/v1/memory", headers=AUTH).json()["items"] == []
    stored = client.get("/api/v1/memory", headers=PERSONAL).json()["items"][0]
    assert stored["value"] == "Synthetic preference"
    assert (
        client.delete(
            "/api/v1/memory/" + stored["id"],
            headers={**PERSONAL, "Idempotency-Key": "delete-created"},
        ).status_code
        == 200
    )
    replay = post(
        client,
        "/memory",
        {"value": "Synthetic preference", "remember": True},
        headers=PERSONAL,
        key="opt-in",
    )
    assert replay.json() == created.json()
    assert client.get("/api/v1/memory", headers=PERSONAL).json()["items"] == []
