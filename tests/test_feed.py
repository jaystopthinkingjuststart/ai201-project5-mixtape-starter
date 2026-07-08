"""
tests/test_feed.py — Mixtape

Tests for the "Friends Listening Now" feed logic.
"""

import pytest
from datetime import datetime, timedelta, timezone
from app import create_app, db
from models import User, Song, ListeningEvent, friendships
from services.feed_service import get_friends_listening_now


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def seed_friends(app):
    """Create two friended users and a song shared by one of them."""
    with app.app_context():
        listener = User(username="listener", email="listener@example.com")
        viewer = User(username="viewer", email="viewer@example.com")
        db.session.add_all([listener, viewer])
        db.session.flush()

        db.session.execute(friendships.insert().values(user_id=viewer.id, friend_id=listener.id))
        db.session.execute(friendships.insert().values(user_id=listener.id, friend_id=viewer.id))

        song = Song(title="Test Song", artist="Test Artist", shared_by=viewer.id)
        db.session.add(song)
        db.session.flush()

        db.session.commit()
        yield {"listener": listener, "viewer": viewer, "song": song}


def test_recent_event_shows_in_listening_now(app, seed_friends):
    """A friend who listened 20 minutes ago should appear as listening now."""
    with app.app_context():
        listener = seed_friends["listener"]
        viewer = seed_friends["viewer"]
        song = seed_friends["song"]

        event = ListeningEvent(
            user_id=listener.id,
            song_id=song.id,
            listened_at=datetime.now(timezone.utc) - timedelta(minutes=20),
        )
        db.session.add(event)
        db.session.commit()

        feed = get_friends_listening_now(viewer.id)
        usernames = [entry["friend"]["username"] for entry in feed]
        assert "listener" in usernames


def test_stale_event_does_not_show_in_listening_now(app, seed_friends):
    """
    A friend who listened 40 minutes ago should NOT appear as listening now.
    Bug caused this to still show up, since the threshold was 24 hours instead
    of 30 minutes — anyone who listened within the past day counted as "now".
    """
    with app.app_context():
        listener = seed_friends["listener"]
        viewer = seed_friends["viewer"]
        song = seed_friends["song"]

        event = ListeningEvent(
            user_id=listener.id,
            song_id=song.id,
            listened_at=datetime.now(timezone.utc) - timedelta(minutes=40),
        )
        db.session.add(event)
        db.session.commit()

        feed = get_friends_listening_now(viewer.id)
        usernames = [entry["friend"]["username"] for entry in feed]
        assert "listener" not in usernames
