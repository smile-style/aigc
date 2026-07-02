from django.urls import reverse


def test_outline_page_renders(client):
    response = client.get(reverse("studio:outline"))

    assert response.status_code == 200
    assert "AI漫剧生成工具" in response.content.decode("utf-8")
    assert "逆袭爽文" in response.content.decode("utf-8")
