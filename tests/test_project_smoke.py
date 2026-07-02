from django.urls import reverse


def test_outline_page_renders(client):
    response = client.get(reverse("studio:outline"))
    content = response.content.decode("utf-8")

    assert response.status_code == 200
    assert "AI漫剧生成工具" in content
    assert "逆袭爽文" in content
    assert "刷新大纲" in content
