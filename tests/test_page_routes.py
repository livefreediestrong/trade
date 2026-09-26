"""Render all shipped pages and verify shared links, assets and autonomous controls."""
from html.parser import HTMLParser
from urllib.parse import urlsplit, unquote

import app as desk


class Page(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.ids, self.links, self.assets = set(), [], set()
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get('id'):
            assert attrs['id'] not in self.ids, f"Duplicate id: {attrs['id']}"
            self.ids.add(attrs['id'])
        if tag == 'a' and attrs.get('href'):
            self.links.append(attrs['href'])
        asset = attrs.get('src') if tag in ('script', 'img') else attrs.get('href') if tag == 'link' else None
        if asset and asset.startswith('/static/'):
            self.assets.add(asset)


def test_every_page_has_valid_links_assets_and_no_avatar_selection():
    client = desk.app.test_client()
    pages = {}
    for name in ('overview', 'auto', 'paper', 'research', 'ticket', 'settings', 'all'):
        path = '/desk/' + name
        response = client.get(path, base_url='http://127.0.0.1:5056')
        assert response.status_code == 200
        pages[path] = Page(response.get_data(as_text=True))
        assert {'moss-fox', 'moss-woman'} <= pages[path].ids
        assert not ({'moss-preview-dialog', 'moss-preview-scene', 'desk-companions-open',
                     'moss-preview-open', 'moss-avatar', 'moss-motion'} & pages[path].ids)
    broken = []
    for path, page in pages.items():
        for link in page.links:
            url = urlsplit(link)
            if url.scheme or url.netloc or not url.fragment:
                continue
            destination = url.path or path
            if destination in pages:
                if unquote(url.fragment) not in pages[destination].ids:
                    broken.append((path, link))
    assert not broken, broken
    for asset in set.union(*(page.assets for page in pages.values())):
        assert client.get(asset, base_url='http://127.0.0.1:5056').status_code == 200, asset
