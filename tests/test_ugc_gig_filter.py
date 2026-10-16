import pytest

from services.ugc_gig_filter import is_ugc_gig, ugc_gig_verdict


@pytest.mark.parametrize("title,desc,reason", [
    ("URGENT: Seeking Journalist Or Podcaster For", "Cover our launch event for our blog.", "journalism"),
    ("PAID ACTORS WANTED - THIS SATURDAY", "Short promo video shoot downtown.", "acting"),
    ("CASTING CALL – WORLDWIDE REMOTE SELF-TAPE", "Send a self-tape for a TikTok ad.", "acting"),
    ("Brand Ambassador", "Hand out samples at our store, post on Instagram.", "ambassador"),
    ("Part-time TikTok Creator — Relationships &", "Part-time role, 20 hours a week filming TikToks.", "employment"),
    ("Short-Form Video Editor", "Edit raw footage into TikTok clips.", "editing"),
    ("Live Host Wanted: Livestream As A", "Host TikTok live sessions.", "live_host"),
    ("VOICE OVER TALENT NEEDED – NO", "Record narration for explainer videos.", "voice"),
    ("Write 10 Skincare SEO Reviews", "Written reviews for our store.", "writing"),
    ("Create AI-Generated Map Video", "Use AI tools to make a video.", "design_ai"),
    ("Nightlife Videographer Needed - DTLA", "Film our club nights.", "production_crew"),
    ("Performance-Based Influencer Sales Challenge", "Earn per sale from your videos.", "sales"),
    ("Hot Tub Store Social Media Management", "Manage and grow our Instagram account.", "smm"),
    ("Remote Female Models Needed", "Photo shoot for our catalogue.", "modeling"),
    ("Product Demo", "Help us with our launch.", "no_ugc_signal"),
])
def test_rejects_non_ugc_gigs(title, desc, reason):
    assert ugc_gig_verdict(title, desc) == (False, reason)


@pytest.mark.parametrize("title,desc", [
    ("Remote Freelance UGC Content Creator", "Film 3 UGC videos for our skincare brand."),
    ("Office UGC App B-Roll", "B-roll of you using our app at your desk."),
    ("Video Creator - Real Human +", "UGC video for a tea brand, with voiceover."),
    ("Instagram Video Content Creator", "Reels showing our necklaces."),
    ("Full Video Testimonial Production", "Talking-head testimonial video."),
    ("Part-Time UGC Creator (Remote)", "Part-time UGC creator, 4 videos a month."),
    ("Paperpal AI UGC Creator", "Make TikTok UGC for our AI writing app."),
    ("Cat Litter UGC Ad", "Film an unboxing and send raw footage plus the edit."),
    ("Street Style Video", "Man-on-the-street interview style TikTok for our drink."),
])
def test_keeps_real_ugc_gigs(title, desc):
    assert ugc_gig_verdict(title, desc) == (True, "ok")


def test_is_ugc_gig_reads_cards_and_scanner_payloads():
    assert is_ugc_gig({"product_name": "TikTok UGC Content Creator", "campaign_description": "3 videos"})
    assert is_ugc_gig({"title": "Instagram Reels Creator", "deliverable_summary": "2 reels"})
    assert not is_ugc_gig({"title": "Brand Ambassador", "deliverable_summary": "event shifts"})
    assert not is_ugc_gig({"title": "TikTok Creator", "deliverable_summary": "3 videos", "creator_niches": ["Actors"]})
