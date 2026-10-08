from django.test import Client


def test_admin_login_page_renders(client: Client) -> None:
    response = client.get("/admin/login/")
    assert response.status_code == 200
