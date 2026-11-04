
import os
import re
from datetime import date

import pandas as pd
import requests
import streamlit as st

st.set_page_config(
    page_title="Horse Racing Predictor",
    page_icon="🏇",
    layout="wide",
)

BASE_URL = "https://api.formfav.com"

st.title("🏇 Horse Racing Predictor")
st.caption("Form-first Australian thoroughbred rankings")

st.warning(
    "Experimental prototype. Form scores are not calibrated "
    "win probabilities and should not be treated as betting advice."
)

def get_api_key():
    try:
        return st.secrets.get("FORMFAV_API_KEY") or os.getenv(
            "FORMFAV_API_KEY"
        )
    except Exception:
        return os.getenv("FORMFAV_API_KEY")

@st.cache_data(ttl=600, show_spinner=False)
def fetch_formfav(endpoint, params, api_key):
    response = requests.get(
        BASE_URL + endpoint,
        headers={"X-API-Key": api_key},
        params=params,
        timeout=30,
    )
    response.raise_for_status()
    return response.json()

def get_items(data, key):
    if isinstance(data, list):
        return data

    if not isinstance(data, dict):
        return []

    if isinstance(data.get(key), list):
        return data[key]

    nested = data.get("data")
    if isinstance(nested, list):
        return nested

    if isinstance(nested, dict):
        if isinstance(nested.get(key), list):
            return nested[key]

    return []

def get_value(obj, *keys, default=None):
    if not isinstance(obj, dict):
        return default

    for key in keys:
        value = obj.get(key)
        if value is not None and value != "":
            return value

    return default

def as_number(value, default=0.0):
    try:
        return float(value)
    except (ValueError, TypeError):
        return default

def win_rate(record):
    if not isinstance(record, dict):
        return 0.0, 0

    starts = max(0, int(as_number(record.get("starts"))))
    wins = max(0, as_number(record.get("wins")))

    if starts == 0:
        return 0.0, 0

    return min(wins / starts, 1.0), starts

def recent_form_score(form):
    digits = re.findall(r"[0-9]", str(form or ""))[-5:]

    if not digits:
        return None

    points = {
        "1": 1.0,
        "2": 0.7,
        "3": 0.5,
        "4": 0.3,
        "5": 0.2,
    }

    weights = range(1, len(digits) + 1)

    return sum(
        w * points.get(position, 0)
        for w, position in zip(weights, digits)
    ) / sum(weights)

def score_runner(runner):
    stats = runner.get("stats") or {}

    career, career_starts = win_rate(
        stats.get("overall", {})
    )

    track, track_starts = win_rate(
        stats.get("track", {})
    )

    distance, distance_starts = win_rate(
        stats.get("distance", {})
    )

    track_distance, td_starts = win_rate(
        stats.get("trackDistance", {})
    )

    recent = recent_form_score(runner.get("form"))

    if recent is None or career_starts == 0:
        return None

    # Smooth small samples toward career performance.
    def smooth(rate, starts):
        return (starts * rate + 5 * career) / (starts + 5)

    track = smooth(track, track_starts)
    distance = smooth(distance, distance_starts)
    track_distance = smooth(track_distance, td_starts)

    barrier = as_number(runner.get("barrier"), 8)
    weight = as_number(runner.get("weight"), 56)

    barrier_score = max(
        0.0, min(1.0, 1 - (barrier - 1) / 16)
    )

    weight_score = max(
        0.0, min(1.0, 1 - (weight - 48) / 16)
    )

    context = (barrier_score + weight_score) / 2

    score = (
        0.35 * recent
        + 0.15 * career
        + 0.15 * track
        + 0.15 * distance
        + 0.10 * track_distance
        + 0.10 * context
    )

    return round(score * 100, 2)

api_key = get_api_key()

if not api_key:
    st.error(
        "FormFav API key not configured. "
        "Add FORMFAV_API_KEY to Streamlit Secrets."
    )
    st.stop()

selected_date = st.date_input(
    "Select racing date",
    value=date.today(),
)

st.subheader("Race meetings")

try:
    meetings_data = fetch_formfav(
        "/v1/form/meetings",
        {
            "date": selected_date.isoformat(),
            "race_code": "gallops",
        },
        api_key,
    )

    meetings = get_items(meetings_data, "meetings")

except requests.RequestException as error:
    st.error(f"Unable to retrieve meetings: {error}")
    st.stop()

if not meetings:
    st.info("No meetings returned for this date.")
    with st.expander("Inspect API response"):
        st.json(meetings_data)
    st.stop()

target_tracks = {
    "flemington",
    "caulfield",
    "moonee valley",
    "mooneevalley",
}

def meeting_name(meeting):
    return str(
        get_value(
            meeting,
            "track",
            "trackName",
            "venue",
            "name",
            default="Unknown",
        )
    )

filtered = [
    meeting
    for meeting in meetings
    if meeting_name(meeting).lower().replace("-", " ").strip()
    in target_tracks
]

if not filtered:
    st.warning(
        "No Flemington, Caulfield or Moonee Valley "
        "meetings were identified."
    )
    with st.expander("Inspect available meetings"):
        st.json(meetings)
    st.stop()

meeting = st.selectbox(
    "Racecourse",
    filtered,
    format_func=meeting_name,
)

races = get_items(meeting, "races")

if not races:
    st.warning("No races were found in the meeting response.")
    with st.expander("Inspect selected meeting"):
        st.json(meeting)
    st.stop()

def race_label(race):
    number = get_value(
        race, "raceNumber", "number", default="?"
    )
    name = get_value(
        race, "raceName", "name", default=""
    )
    return f"Race {number} — {name}"

race = st.selectbox(
    "Select race",
    races,
    format_func=race_label,
)

track_slug = get_value(
    meeting,
    "slug",
    "trackSlug",
    default=meeting_name(meeting).lower().replace(" ", "-"),
)

race_number = get_value(
    race, "raceNumber", "number"
)

if st.button("Analyse race", type="primary"):

    try:
        race_data = fetch_formfav(
            "/v1/form",
            {
                "date": selected_date.isoformat(),
                "track": track_slug,
                "race": race_number,
                "race_code": "gallops",
                "country": "au",
            },
            api_key,
        )

        runners = get_items(race_data, "runners")

    except requests.RequestException as error:
        st.error(f"Unable to retrieve race form: {error}")
        st.stop()

    if not runners:
        st.warning("No runners returned.")
        with st.expander("Inspect race API response"):
            st.json(race_data)
        st.stop()

    results = []

    for runner in runners:
        if not isinstance(runner, dict):
            continue

        if runner.get("scratched") is True:
            continue

        score = score_runner(runner)

        results.append({
            "Horse": get_value(
                runner,
                "name",
                "runnerName",
                "horseName",
                default="Unknown",
            ),
            "Form Score": score,
            "Recent Form": runner.get("form", ""),
            "Barrier": runner.get("barrier"),
            "Weight": runner.get("weight"),
            "Jockey": runner.get("jockey"),
            "Trainer": runner.get("trainer"),
        })

    if not results:
        st.warning("No eligible runners were found.")
        st.stop()

    df = pd.DataFrame(results)

    df = df.sort_values(
        "Form Score",
        ascending=False,
        na_position="last",
    ).reset_index(drop=True)

    df.insert("Rank", 0, range(1, len(df) + 1))

    st.subheader("Best Chance — Form Rankings")

    st.dataframe(
        df,
        hide_index=True,
        use_container_width=True,
    )

    st.caption(
        "Higher form score means a stronger preliminary "
        "rating. Scores are not win probabilities. "
        "Missing form produces an unrated runner."
    )

    st.download_button(
        "Download race rankings",
        df.to_csv(index=False),
        file_name="horse_racing_rankings.csv",
        mime="text/csv",
    )

    with st.expander("Inspect raw race data"):
        st.json(race_data)

st.divider()

st.caption(
    "Horse Racing Predictor v0.1 | "
    "Form-first experimental model | "
    "No market odds used in ranking"
)
