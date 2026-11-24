"""Guard: public kits never make outbound requests while serving a page.

Nov 24 2026: resolving TikTok share links (tiktok.com/t/...) inside the kit request held its DB connection open
during the TikTok calls; the connection was closed and kits returned 500. The share-link code was removed entirely.
Share links render with a play button that opens TikTok (frontend KitPostEmbed).
"""
import inspect


def test_kit_routes_have_no_share_link_resolution():
    import portfolio_routes
    import public_routes
    for mod in (portfolio_routes, public_routes):
        src = inspect.getsource(mod)
        assert 'kit_links' not in src and 'expand_kit_examples' not in src and 'fix_kit_share_links' not in src


def test_public_kit_route_makes_no_http_requests():
    import portfolio_routes
    src = inspect.getsource(portfolio_routes.get_public_kit)
    assert 'requests.' not in src and 'urlopen' not in src
