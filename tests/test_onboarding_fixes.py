"""Onboarding fixes (Nov 28): TikTok proof kept by user, welcome brand 'about', CORS per origin on cached routes."""
from services import oauth_proof as op


class Cur:
    def __init__(self, db):
        self.db, self.last = db, None

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        self.last = None
        if s.startswith("CREATE TABLE"):
            return
        if s.startswith("INSERT INTO onboarding_oauth_proof"):
            uid, platform, handle, followers, posts, avatar, verified = params
            self.db[uid] = (platform, handle, followers, posts, avatar, verified)
        elif s.startswith("SELECT platform, handle"):
            row = self.db.get(params[0])
            self.last = row[:5] if row and row[5] else None
        else:
            raise AssertionError(s)

    def fetchone(self):
        return self.last


class Conn:
    def __init__(self):
        self.db = {}

    def cursor(self, *a, **k):
        return Cur(self.db)

    def commit(self):
        pass

    def rollback(self):
        pass


def test_proof_saved_by_user_and_read_back_as_step1_session():
    conn = Conn()
    op.save(conn, 6629, "tiktok", {"username": "@growyourself37", "follower_count": 5386, "media_count": 844,
                                   "avatar_url": "https://x/a.jpg"}, True)
    proof = op.load(conn, 6629)
    assert proof["handle"] == "growyourself37" and proof["followers"] == 5386
    sess = op.as_session(proof)
    assert sess["social_verification_result"]["verified"] is True
    assert sess["onboarding_quality"] == {"handle": "growyourself37", "platform": "tiktok", "followers": 5386}

    from services.profile_quality import resolve_verified_onboarding_followers
    assert resolve_verified_onboarding_followers(
        username="growyourself37", platform="tiktok", quality_session=sess["onboarding_quality"],
        verification_result=sess["social_verification_result"]) == 5386


def test_no_proof_for_other_users_or_failed_checks():
    conn = Conn()
    op.save(conn, 1, "tiktok", {"username": "monica"}, False)
    assert op.load(conn, 1) is None
    assert op.load(conn, 2) is None
    assert op.load(conn, None) is None
    op.save(conn, 3, "tiktok", {"username": ""}, True)
    assert op.load(conn, 3) is None


def test_welcome_about_keeps_whole_sentences():
    from services.creator_tracker import welcome_about
    assert welcome_about(None) == ""
    assert welcome_about("  Clean   protein bars. ") == "Clean protein bars."
    long = "Naked Nutrition makes clean, single-ingredient supplements for athletes. " * 6
    out = welcome_about(long)
    assert len(out) <= 220 and out.endswith(".")
    words = welcome_about("word " * 80)
    assert words.endswith("…") and len(words) <= 221


def test_cached_public_routes_vary_by_origin():
    from flask import Flask
    import public_routes as pr
    app = Flask(__name__)

    @app.route("/api/public/brands-replying")
    def x():
        return "ok"

    for origin in ("https://newcollab.co", "https://app.newcollab.co"):
        with app.test_request_context("/api/public/brands-replying", headers={"Origin": origin}):
            resp = pr.add_cors_headers(app.make_response("ok"))
        assert resp.headers["Access-Control-Allow-Origin"] == origin
        assert "Origin" in resp.headers["Vary"]
