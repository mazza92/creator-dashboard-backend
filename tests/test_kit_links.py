from services.kit_links import (
    canonical_tiktok, expand_kit_examples, has_short_links, is_short_tiktok, resolve_short_links,
)

FULL = 'https://www.tiktok.com/@mamatlynne/video/7400000000000000001?is_from_webapp=1&sender_device=pc'


def test_short_link_patterns():
    assert is_short_tiktok('https://www.tiktok.com/t/ZTy2LoCQw/')
    assert is_short_tiktok('https://vm.tiktok.com/ZMabc/')
    assert is_short_tiktok('https://vt.tiktok.com/ZSabc/')
    assert not is_short_tiktok('https://www.tiktok.com/@a/video/123')
    assert not is_short_tiktok('https://www.instagram.com/reel/abc/')
    assert not is_short_tiktok(None)


def test_canonical_strips_tracking():
    assert canonical_tiktok(FULL) == 'https://www.tiktok.com/@mamatlynne/video/7400000000000000001'
    assert canonical_tiktok('https://www.tiktok.com/@a.b/photo/9') == 'https://www.tiktok.com/@a.b/photo/9'
    assert canonical_tiktok('https://www.tiktok.com/login') is None


def test_expand_swaps_resolved_and_keeps_failures():
    theme = {
        'example_posts': [
            {'url': 'https://www.tiktok.com/t/OK1/', 'title': 'Hi', 'description': ''},
            {'url': 'https://www.tiktok.com/t/BAD/', 'title': '', 'description': ''},
            {'url': 'https://www.instagram.com/reel/x/', 'title': '', 'description': ''},
        ],
        'examples': ['https://www.tiktok.com/t/OK1/', 'https://www.tiktok.com/t/BAD/', 'https://www.instagram.com/reel/x/'],
        'about': 'kept',
    }

    def follow(u):
        if 'BAD' in u:
            raise TimeoutError('slow')
        return FULL

    out, changed = expand_kit_examples(theme, follow=follow)
    assert changed
    assert out['example_posts'][0] == {'url': 'https://www.tiktok.com/@mamatlynne/video/7400000000000000001',
                                       'title': 'Hi', 'description': ''}
    assert out['example_posts'][1]['url'] == 'https://www.tiktok.com/t/BAD/'
    assert out['examples'][0].endswith('/video/7400000000000000001')
    assert out['examples'][2] == 'https://www.instagram.com/reel/x/'
    assert out['about'] == 'kept'
    assert has_short_links(theme) and not has_short_links({'examples': out['examples'][:1]})


def test_nothing_to_do_makes_no_requests():
    calls = []
    theme = {'examples': ['https://www.tiktok.com/@a/video/1'], 'example_posts': []}
    out, changed = expand_kit_examples(theme, follow=lambda u: calls.append(u))
    assert (out, changed) == (theme, False) and calls == []
    assert resolve_short_links([], follow=lambda u: calls.append(u)) == {}


def test_redirect_to_non_post_is_ignored():
    assert resolve_short_links(['https://vm.tiktok.com/Z/'], follow=lambda u: 'https://www.tiktok.com/') == {}


class _Cur:
    def __init__(self, rows, log):
        self.rows, self.log = rows, log

    def execute(self, sql, params=None):
        self.log.append((sql.split()[0], params))

    def fetchall(self):
        return self.rows


class _Conn:
    def __init__(self, rows, log):
        self.rows, self.log, self.open = rows, log, True

    def cursor(self):
        return _Cur(self.rows, self.log)

    def commit(self):
        self.log.append(('COMMIT', None))

    def rollback(self):
        pass

    def close(self):
        self.open = False


def test_cron_fix_resolves_with_no_connection_open():
    from services.kit_links import fix_kit_share_links
    theme = {'example_posts': [{'url': 'https://www.tiktok.com/t/OK1/', 'title': '', 'description': ''}],
             'examples': ['https://www.tiktok.com/t/OK1/']}
    log, conns = [], []

    def get_conn():
        c = _Conn([(283, theme)], log)
        conns.append(c)
        return c

    def follow(u):
        assert all(not c.open for c in conns), 'a DB connection was open during the TikTok request'
        return FULL

    out = fix_kit_share_links(get_conn, follow=follow)
    assert out == {'kits_checked': 1, 'kits_fixed': 1}
    updates = [p for op, p in log if op == 'UPDATE']
    assert updates and updates[0][1] == 283
    assert updates[0][0].adapted['examples'] == ['https://www.tiktok.com/@mamatlynne/video/7400000000000000001']
    assert all(not c.open for c in conns)
