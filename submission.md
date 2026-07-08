# Mixtape — Submission

## AI Usage

i used claude code throughout this project, but tried to keep it in an explain/trace role rather than a diagnose role, since that's what actually held up.

for the codebase map, i had it read through app.py, models.py, and each route/service file and explain what each one does and how they connect, then i traced one flow myself (add a song to a playlist, which triggers a notification to the sharer) by having it walk the call chain file by file, in order, rather than jumping straight to the function it suspected. i pushed back once when it wrote the whole map before walking me through the reasoning first, since i wanted to understand the "why" behind each step before it got written down anywhere.

for each bug, i had it read the route first, then follow the exact call chain into the service layer, one file at a time, instead of asking it to just go find the bug. that mattered on issue #4, where reading rate_song next to add_to_playlist side by side, in the same file, is what actually showed the missing notification call. if i'd just asked "what's wrong with rate_song," it might have guessed at something plausible without the direct comparison that made it obvious.

the most useful failure was on issue #2. i asked it to reproduce "friends listening now shows people from yesterday" and its first attempt wrote a script that recalculated the "expected" result using the same 24 hour cutoff the code already used, so naturally it matched and looked like the bug didn't reproduce. that was a real dead end, not a manufactured one, it took going back and rereading a comment in seed_data.py ("recent events within the past 30 minutes") that had already been read earlier in the session but wasn't weighed as the actual spec until this point. i had it lay out that reasoning explicitly, then verify against a friend pairing where the stale event wasn't masked by a fresh one, before i accepted the diagnosis.

for the fixes themselves, i had it verify boundary conditions on both sides for the two threshold-shaped bugs, songs at the edges of the playlist and events just inside/outside the 30 minute window, and grep for every caller of a function before changing it, rather than trusting that a fix in isolation was safe. i read every diff myself before it was applied and asked it to re-run the full test suite after each change to confirm nothing else broke. i wrote the final root cause and reproduction descriptions in my own words rather than accepting its first draft verbatim, since the assignment is explicitly about being able to explain the reasoning myself.

## Codebase Map

### Architecture

The app is a Flask API organized in three layers, `routes → services → models`:

- **`routes/*.py`** — HTTP layer. Each file is a Flask blueprint for one resource (`songs`, `playlists`, `users`, `feed`). Routes parse the request, call a service function, and translate the result (or a `ValueError`) into a JSON response. They contain no business logic themselves.
- **`services/*.py`** — business logic layer. This is where the actual rules live (streak calculation, playlist ordering, search, notifications, feed aggregation). Bugs in *behavior* almost always live here, not in `routes/`.
- **`models.py`** — all SQLAlchemy models and association tables in one file.
- **`app.py`** — the Flask application factory (`create_app`) and the shared `db = SQLAlchemy()` singleton that every other module imports (`from app import db`). Blueprints are registered here.
- **`seed_data.py`** — populates the dev database with sample users/songs/playlists.
- **`tests/`** — one test file per feature area: `test_playlists.py`, `test_search.py`, `test_streaks.py`. Notably there is no test file for `feed` or `notifications` — those areas have no automated safety net.

### File-by-file

| File | Role |
|---|---|
| `app.py` | Flask app factory; creates the shared `db` object; registers blueprints; creates tables on startup. |
| `models.py` | ORM models: `User`, `Song`, `Tag`, `Playlist`, `Rating`, `ListeningEvent`, `Notification`, plus association tables `friendships`, `song_tags`, `playlist_entries` (the last one carries ordering via a `position` column). |
| `routes/songs.py` | `GET /songs/search`, `GET /songs/<id>`, `POST /songs/<id>/rate`, `POST /songs/<id>/listen`. |
| `routes/playlists.py` | `POST /playlists/`, `GET /playlists/<id>`, `GET /playlists/<id>/songs`, `POST /playlists/<id>/songs` (add a song). |
| `routes/users.py` | `GET /users/<id>`, `GET /users/<id>/streak`, `GET /users/<id>/notifications`, `POST /users/notifications/<id>/read`. |
| `routes/feed.py` | `GET /feed/<user_id>/listening-now`, `GET /feed/<user_id>/activity`. |
| `services/playlist_service.py` | Create playlists, fetch a playlist's ordered songs, fetch a user's playlists. |
| `services/search_service.py` | Case-insensitive search over song title/artist; fetch a single song. |
| `services/streak_service.py` | Records a listening event and updates `User.listening_streak` based on calendar-day gaps. |
| `services/feed_service.py` | "Friends listening now" (last 24h, deduped to one song per friend) and a general activity feed (most recent N events, no time filter). |
| `services/notification_service.py` | Creates/reads/marks-read `Notification` rows; also owns `add_to_playlist` (which triggers a notification) and, a bit inconsistently, `rate_song` (rating logic with no obvious reason to live in the notification module). |

### Data flow: adding a song to a playlist → notifying the sharer

This is the one flow in the app that crosses route → two services → model, so it's the clearest example of the layering:

1. **Client** calls `POST /playlists/<playlist_id>/songs` with `{song_id, added_by}` — handled by `add_song()` in `routes/playlists.py:44`.
2. The route does only request validation (checks `song_id`/`added_by` are present) and delegates to `add_to_playlist(playlist_id, song_id, added_by)` in `services/notification_service.py:35`.
3. `add_to_playlist` looks up the `Song`, the adding `User`, and the `Playlist` (raising `ValueError` → HTTP 400 if any are missing — this is how the service layer communicates domain errors up to the route layer, which is a pattern used consistently across the app).
4. If the song isn't already on the playlist, it appends it to `playlist.songs` (the `playlist_entries` many-to-many table) and commits.
5. It then compares `song.shared_by` (the `User.id` who originally shared the song, stored on the `Song` row at creation time) against `added_by`. If they differ, it calls `create_notification(...)`, which inserts a `Notification` row for the original sharer with `notification_type="song_added_to_playlist"`.
6. That notification is later surfaced to the sharer via `GET /users/<user_id>/notifications` (`routes/users.py:29` → `get_notifications()` in `notification_service.py`), and can be dismissed via `POST /users/notifications/<id>/read` → `mark_as_read()`.

Key pattern this reveals: **services call other services directly** (notification_service imports from playlist_service and models), and **routes never talk to models directly** except in `routes/users.py`, which reaches into `db`/`User` directly for the simple `GET /users/<id>` lookup rather than going through a service — the one inconsistency in an otherwise clean layering.

### Other patterns worth noting

- Every model has a `to_dict()` method used to serialize directly to JSON in routes — there's no separate serialization layer.
- IDs are UUID strings (`generate_uuid()`), not auto-increment integers.
- Domain/validation errors are raised as `ValueError` in services and caught in routes to produce 4xx responses — this convention is used uniformly, so any new route should follow it rather than inventing new error handling.

## Bug Reproductions and Root Cause Analysis

### Issue #5 — the last song in a playlist never shows up

how i reproduced it: i seeded a playlist with five songs added in order, then called `get_playlist_songs`. running the existing test suite (`pytest tests/test_playlists.py`) already sets this state up through its fixtures, so i didn't need to write anything new. the output only had four songs back, and the missing one was always the last song by position, track 5, never a random one from the middle. that consistency is what pointed me toward a slicing problem instead of something like a broken filter or a join issue.

how i found the root cause: i started at the route, `get_songs` in `routes/playlists.py`, which just calls `get_playlist_songs` and returns whatever it gets back, so there was nothing to suspect there. that led me into `services/playlist_service.py`, where i read the whole function instead of skimming it. the query builds a join between `Song` and `playlist_entries`, filters by playlist id, and orders ascending by position, which matches what the docstring above it promises. all of that checked out. the line right after the query, `return [song.to_dict() for song in songs[:-1]]`, is what confirmed it for me. the query already produces the correct ordered list, and the slice on that list is the only thing that touches the result afterward, so it had to be the cause rather than just a suspicious spot.

the root cause: the function queries and orders the songs correctly, but builds its return value from `songs[:-1]` instead of `songs`. `[:-1]` drops the last item of any non-empty list, so whichever song sits last in position order gets silently cut from every response, no matter how many songs are in the playlist.

my fix and side effect check: i changed `songs[:-1]` to `songs` so the full ordered list gets returned. before committing i grepped for every place `get_playlist_songs` is called, since the assignment asks to check for side effects. it's called from the route and from the existing tests, and it's also imported (but never actually called) inside `add_to_playlist` in `notification_service.py`, which is unrelated dead code i left alone since touching it wasn't part of this fix. i ran the full test suite after the change, both playlist tests now pass and the only remaining failure is the unrelated sunday streak bug, so nothing else regressed.

### Issue #4 — no notification when a friend rates your song

how i reproduced it: i created two users and one song shared by the first user, then used flask's test client to hit the real routes the way an actual client would, `POST /songs/<id>/rate` followed by `GET /users/<id>/notifications`. i went through the http layer on purpose rather than calling the service function directly, since the bug report itself describes something a user noticed happening (or not happening), so reproducing it through the same interface felt like stronger evidence. the rating came back with a 201 and saved fine, but the notifications list for the sharer stayed empty. comparing that against `add_to_playlist`, which does call `create_notification`, made the gap obvious.

how i found the root cause: the route, `rate` in `routes/songs.py`, is another thin pass-through, it just calls `rate_song` and serializes the result, so there was nothing to find there. `rate_song` lives in `services/notification_service.py`, right above `add_to_playlist`, which is the function that already notifies correctly for the playlist case. reading them side by side is what nailed it down. `add_to_playlist` ends with a check, `if song.shared_by != added_by_user_id`, followed by a call to `create_notification`. `rate_song` fetches the same `song` row, so `song.shared_by` is sitting right there, builds or updates the `Rating`, commits, and returns, with no equivalent block anywhere in the function. i wasn't just looking at a function that seemed suspicious, i was looking at the one piece of logic present in its sibling function and completely absent here.

the root cause: `rate_song` never calls `create_notification`. it has every piece of information it needs to do so, `song.shared_by` from the lookup it already performs and `rater.username` from the user it already fetches, but the function returns right after committing the rating with no notification step at all. it's a missing call, not a wrong condition or a bad comparison.

my fix and side effect check: i added the same notify-unless-you're-the-sharer block that `add_to_playlist` uses, right after the commit and before the return, using a new `notification_type` of `"song_rated"`. i grepped for every caller of `rate_song` and found exactly one, the rate route, so there was no other code path that would be surprised by the new side effect. i re-ran my reproduction script and confirmed a friend's rating now creates a notification, then added a self-rating case to the same script to confirm rating your own song still creates zero notifications, matching the guard `add_to_playlist` already uses. the full test suite still passes except for the unrelated sunday streak bug, so nothing else broke.

### Issue #2 — friends listening now shows people from yesterday

how i reproduced it: this one took a couple of wrong turns before i actually reproduced it. my first attempt compared the feed's output against a script that recalculated the "expected" friends using the same 24 hour cutoff the code already uses, so of course everything matched, i was just checking that the code agreed with itself. the real spec was sitting in a comment i'd already read but hadn't weighed properly, seed_data.py describes events "within the past 30 minutes" as the ones that should count as recent, which is a much tighter window than the 24 hour `RECENT_THRESHOLD` actually used in `feed_service.py`. once i picked a friend pairing where the only listening event wasn't masked by a fresher one, kenji and nova, the feed for kenji showed nova based on an event that was 2.5 hours old. that's nowhere close to "now," and depending on the time of day it would read as yesterday to a user, which matches the report exactly.

how i found the root cause: the route, `listening_now` in `routes/feed.py`, is a pass-through as usual, so i went straight into `get_friends_listening_now` in `services/feed_service.py`. i read the query, the filter, and the dedup step carefully since this bug is about wrong data showing up rather than a crash, and all three pieces do exactly what they claim, the query joins on the right friend ids, filters by `listened_at >= cutoff`, orders by most recent, and the dedup loop correctly keeps only the newest event per friend. nothing in that logic is wrong. what's wrong sits one line above the function, `RECENT_THRESHOLD = timedelta(hours=24)`. i only caught it by going back to seed_data.py and actually weighing its comment, "within the past 30 minutes," as the real spec instead of just data setup. that comment describes what a human decided "recent" should mean when they built the feature, and it doesn't match the constant the code uses by a wide margin.

the root cause: `RECENT_THRESHOLD` is set to 24 hours, but the feature is supposed to mean something closer to 30 minutes based on how the seed data defines "recent." every other part of the function behaves correctly relative to whatever `RECENT_THRESHOLD` says, so this isn't a comparison bug or an off by one, it's a constant that's wrong by roughly a factor of 48, which lets anyone who listened up to a full day ago still count as "listening now."

my fix and side effect check: i changed `RECENT_THRESHOLD` from `timedelta(hours=24)` to `timedelta(minutes=30)`. i grepped the file and confirmed that constant is used in exactly one place, so nothing else could be affected by changing it. since this is a boundary condition bug, i tested both sides deliberately, an event at 20 minutes old showed up, the same event moved to 40 minutes old disappeared, and the original 2.5 hour case from the reproduction also disappeared as expected. i also reran the seeded database and confirmed `get_activity_feed`, the sibling function in the same file that doesn't use `RECENT_THRESHOLD` at all, still returns its usual unfiltered results, and the fresh events for nova's friends (10 to 20 minutes old) still show up correctly. the full test suite still only fails on the unrelated sunday streak bug.
