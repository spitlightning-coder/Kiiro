import json

from dave.freegames import make_embed, merge, parse_epic, parse_gamerpower
from dave.quiz import BANK, Question, bank_questions, parse_questions
from dave.serverdata import ServerData

EPIC = {"data": {"Catalog": {"searchStore": {"elements": [
    {"title": "Mark of the Deep", "id": "m1", "description": "Piratenabenteuer",
     "keyImages": [{"type": "OfferImageWide", "url": "https://img/wide.jpg"}],
     "catalogNs": {"mappings": [{"pageSlug": "mark-of-the-deep-f7dc08", "pageType": "productHome"}]},
     "price": {"totalPrice": {"discountPrice": 0, "originalPrice": 1950}},
     "promotions": {"promotionalOffers": [{"promotionalOffers": [
         {"startDate": "2026-11-19T16:00:00.000Z", "endDate": "2026-11-26T16:00:00.000Z"}]}]}},
    {"title": "Firestone", "id": "f2P", "price": {"totalPrice": {"discountPrice": 0, "originalPrice": 0}},
     "promotions": {"promotionalOffers": []}, "productSlug": "firestone"},
    {"title": "Bald gratis", "id": "soon", "productSlug": "soon",
     "price": {"totalPrice": {"discountPrice": 1349, "originalPrice": 1349}},
     "promotions": {"promotionalOffers": [], "upcomingPromotionalOffers": [{}]}},
]}}}}

GP = [
    {"id": 1, "title": "Cool Game (Steam) Giveaway", "type": "Game", "status": "Active", "platforms": "PC, Steam",
     "worth": "$9.99", "open_giveaway_url": "https://gp/1", "image": "https://gp/1.jpg", "end_date": "2026-11-30 23:59:00"},
    {"id": 2, "title": "Mark of the Deep Giveaway", "type": "Game", "status": "Active",
     "platforms": "PC, Epic Games Store", "worth": "$19.99", "open_giveaway_url": "https://gp/2", "end_date": "N/A"},
    {"id": 3, "title": "Itch Thing", "type": "Game", "status": "Active", "platforms": "PC, Itch.io",
     "open_giveaway_url": "https://gp/3"},
    {"id": 4, "title": "Some DLC", "type": "DLC", "status": "Active", "platforms": "PC, Steam",
     "open_giveaway_url": "https://gp/4"},
]


def test_parse_epic_only_real_free_games():
    (g,) = parse_epic(EPIC)
    assert g.title == "Mark of the Deep" and g.key == "epic:m1"
    assert g.url == "https://store.epicgames.com/de/p/mark-of-the-deep-f7dc08"
    assert g.worth == "19,50 €" and g.image == "https://img/wide.jpg" and g.ends


def test_parse_gamerpower_filters_and_cleans():
    games = parse_gamerpower(GP)
    assert [(g.title, g.store) for g in games] == [("Cool Game", "Steam"), ("Mark of the Deep", "Epic Games")]
    assert games[1].ends is None
    assert parse_gamerpower({"status": 0}) == []


def test_merge_dedupes_and_embed():
    games = merge(parse_epic(EPIC), parse_gamerpower(GP))
    assert [g.title for g in games] == ["Mark of the Deep", "Cool Game"]
    e = make_embed(games[0])
    assert "Mark of the Deep" in e.title and any("gratis" in f.value for f in e.fields)


def test_parse_questions_validates():
    raw = "<think>x</think>" + json.dumps({"fragen": [
        {"frage": "2+2?", "antworten": ["4", "3", "5", "22"], "richtig": 0, "info": "Mathe"},
        {"frage": "Kaputt", "antworten": ["a", "b"], "richtig": 0},
        {"frage": "Doppelt", "antworten": ["a", "a", "b", "c"], "richtig": 1},
        {"frage": "Index", "antworten": ["a", "b", "c", "d"], "richtig": 7},
    ]})
    qs = parse_questions(raw, 5)
    assert len(qs) == 1 and qs[0].answers[qs[0].correct] == "4"
    assert parse_questions("quatsch", 5) == [] and parse_questions(None, 5) == []


def test_shuffle_keeps_correct_answer():
    q = Question("?", ["richtig", "b", "c", "d"], 0)
    for _ in range(20):
        s = q.shuffled()
        assert s.answers[s.correct] == "richtig" and sorted(s.answers) == sorted(q.answers)


def test_bank_is_valid():
    assert len(BANK) >= 25
    for text, answers, idx, info in BANK:
        assert len(answers) == 4 and 0 <= idx < 4 and len(set(answers)) == 4 and text.endswith("?")
    assert len(bank_questions(5)) == 5


def test_quiz_scores(tmp_path):
    d = ServerData(tmp_path)
    d.add_quiz_result(1, 7, "Anna", 15, True)
    d.add_quiz_result(1, 7, "Anna", 0, False)
    d.add_quiz_result(1, 8, "Ben", 10, True)
    assert d.quiz_top(1) == [("Anna", 15, 1, 2), ("Ben", 10, 1, 1)]
