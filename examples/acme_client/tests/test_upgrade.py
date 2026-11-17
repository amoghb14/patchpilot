from app.upgrade import upgrade_to_pro


def test_upgrade_sets_plan(client):
    updated = upgrade_to_pro(client, "cus_2")
    assert updated["plan"] == "pro"
