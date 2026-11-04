import os
import re

from datetime import date, timedelta

import pandas as pd
import requests
import streamlit as st


# ============================================================
# APPLICATION CONFIGURATION
# ============================================================

st.set_page_config(
    page_title="Horse Racing Predictor",
    page_icon="🏇",
    layout="wide",
)

BASE_URL = "https://api.formfav.com"

TARGET_TRACKS = {
    "flemington",
    "caulfield",
    "moonee valley",
    "mooneevalley",
}

st.title("🏇 Horse Racing Predictor")

st.caption(
    "Australian Thoroughbred Racing | "
    "Independent Form-Based Rankings"
)

st.warning(
    "Experimental prediction model. Form scores are not "
    "calibrated win probabilities and should not be "
    "interpreted as guaranteed betting value."
)


# ============================================================
# API CONFIGURATION
# ============================================================

def get_api_key():

    try:
        return (
            st.secrets.get("FORMFAV_API_KEY")
            or os.getenv("FORMFAV_API_KEY")
        )

    except Exception:
        return os.getenv("FORMFAV_API_KEY")


@st.cache_data(ttl=600, show_spinner=False)
def fetch_formfav(endpoint, params, api_key):

    response = requests.get(
        BASE_URL + endpoint,
        headers={
            "X-API-Key": api_key
        },
        params=params,
        timeout=30,
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# DATA EXTRACTION HELPERS
# ============================================================

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


# ============================================================
# MEETING HELPERS
# ============================================================

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


def normalize_track(name):

    name = str(name).lower()

    name = name.replace("-", " ")
    name = name.replace("_", " ")

    name = re.sub(r"\s+", " ", name)

    return name.strip()


def is_target_track(meeting):

    name = normalize_track(
        meeting_name(meeting)
    )

    if name in TARGET_TRACKS:
        return True

    for target in TARGET_TRACKS:

        if name.startswith(target + " "):
            return True

    return False


def meeting_country(meeting):

    return str(
        get_value(
            meeting,
            "country",
            "countryCode",
            default="",
        )
    ).lower()


def is_australian_meeting(meeting):

    country = meeting_country(meeting)

    return country in {
        "au",
        "aus",
        "australia",
    }


def race_label(race):

    number = get_value(
        race,
        "raceNumber",
        "number",
        default="?",
    )

    name = get_value(
        race,
        "raceName",
        "name",
        default="",
    )

    distance = get_value(
        race,
        "distance",
        default="",
    )

    return (
        f"Race {number} — "
        f"{name} {distance}"
    )


# ============================================================
# EXISTING FORM SCORING ENGINE
# ============================================================

def win_rate(record):

    if not isinstance(record, dict):
        return 0.0, 0

    starts = max(
        0,
        int(as_number(record.get("starts")))
    )

    wins = max(
        0,
        as_number(record.get("wins"))
    )

    if starts == 0:
        return 0.0, 0

    return min(wins / starts, 1.0), starts


def recent_form_score(form):

    digits = re.findall(
        r"[0-9]",
        str(form or "")
    )[-5:]

    if not digits:
        return None

    points = {
        "1": 1.0,
        "2": 0.7,
        "3": 0.5,
        "4": 0.3,
        "5": 0.2,
    }

    weights = range(
        1,
        len(digits) + 1
    )

    return sum(
        w * points.get(position, 0)
        for w, position in zip(weights, digits)
    ) / sum(weights)


def score_runner(runner):

    stats = runner.get("stats") or {}

    if not isinstance(stats, dict):
        stats = {}

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

    recent = recent_form_score(
        runner.get("form")
    )

    if recent is None or career_starts == 0:
        return None

    # Smooth small samples toward career performance.

    def smooth(rate, starts):

        return (
            starts * rate + 5 * career
        ) / (starts + 5)

    track = smooth(
        track,
        track_starts
    )

    distance = smooth(
        distance,
        distance_starts
    )

    track_distance = smooth(
        track_distance,
        td_starts
    )

    barrier = as_number(
        runner.get("barrier"),
        8
    )

    weight = as_number(
        runner.get("weight"),
        56
    )

    barrier_score = max(
        0.0,
        min(
            1.0,
            1 - (barrier - 1) / 16
        )
    )

    weight_score = max(
        0.0,
        min(
            1.0,
            1 - (weight - 48) / 16
        )
    )

    context = (
        barrier_score + weight_score
    ) / 2

    score = (
        0.35 * recent
        + 0.15 * career
        + 0.15 * track
        + 0.15 * distance
        + 0.10 * track_distance
        + 0.10 * context
    )

    return round(score * 100, 2)


# ============================================================
# API KEY VALIDATION
# ============================================================

api_key = get_api_key()

if not api_key:

    st.error(
        "FormFav API key not configured. "
        "Add FORMFAV_API_KEY to Streamlit Secrets."
    )

    st.stop()


# ============================================================
# DATE SELECTION
# ============================================================

st.header("📅 Select Racing Date")

selected_date = st.date_input(
    "Racing date",
    value=date.today(),
)

st.caption(
    "Select a future or historical racing date."
)


# ============================================================
# FETCH MEETINGS
# ============================================================

st.header("🏟️ Race Meetings")

try:

    with st.spinner("Retrieving race meetings..."):

        meetings_data = fetch_formfav(
            "/v1/form/meetings",
            {
                "date": selected_date.isoformat(),
                "race_code": "gallops",
                "country": "au",
            },
            api_key,
        )

        meetings = get_items(
            meetings_data,
            "meetings"
        )

except requests.RequestException as error:

    st.error(
        f"Unable to retrieve meetings: {error}"
    )

    st.stop()

except ValueError as error:

    st.error(
        f"Invalid API response: {error}"
    )

    st.stop()


# ============================================================
# API DIAGNOSTICS
# ============================================================

with st.expander(
    "🔍 API Meeting Diagnostics",
    expanded=False,
):

    st.write(
        "Requested date:",
        selected_date.isoformat()
    )

    st.write(
        "Number of meetings returned:",
        len(meetings)
    )

    diagnostic_rows = []

    for meeting in meetings:

        if not isinstance(meeting, dict):
            continue

        races_list = (
            meeting.get("races") or []
        )

        timezone = None
        first_race_time = None

        if (
            isinstance(races_list, list)
            and races_list
            and isinstance(races_list[0], dict)
        ):

            timezone = races_list[0].get(
                "timezone"
            )

            first_race_time = races_list[0].get(
                "startTime"
            )

        diagnostic_rows.append({

            "Track": meeting_name(meeting),

            "Country": meeting_country(meeting),

            "Slug": meeting.get("slug"),

            "Timezone": timezone,

            "First Race UTC": first_race_time,

            "Race Count": (
                len(races_list)
                if isinstance(races_list, list)
                else 0
            ),

        })

    if diagnostic_rows:

        diagnostics_df = pd.DataFrame(
            diagnostic_rows
        )

        st.dataframe(
            diagnostics_df,
            hide_index=True,
            use_container_width=True,
        )

        country_counts = (
            diagnostics_df["Country"]
            .value_counts()
        )

        st.write(
            "Meetings by country:"
        )

        st.dataframe(
            country_counts.rename(
                "Meeting Count"
            ),
            use_container_width=True,
        )

    else:

        st.info(
            "No meetings returned for this date."
        )

    with st.expander(
        "Inspect raw API response"
    ):

        st.json(meetings_data)


# ============================================================
# FILTER AUSTRALIAN MEETINGS
# ============================================================

australian_meetings = [

    meeting

    for meeting in meetings

    if (
        isinstance(meeting, dict)
        and is_australian_meeting(meeting)
    )

]

filtered = [

    meeting

    for meeting in australian_meetings

    if is_target_track(meeting)

]


# ============================================================
# MISSING MEETING DIAGNOSTICS
# ============================================================

if not filtered:

    st.warning(
        "No Flemington, Caulfield or Moonee Valley "
        "meetings were identified for this date."
    )

    st.write(
        "Australian meetings returned:",
        len(australian_meetings)
    )

    if not australian_meetings:

        st.error(
            "The API has not returned any meetings "
            "identified as Australian."
        )

        st.info(
            "The country=au parameter was supplied, "
            "but the provider may not support this "
            "filter or may not have Australian "
            "meetings available for this date."
        )

    else:

        st.info(
            "Australian meetings were returned, "
            "but none matched the configured "
            "Victorian racecourses."
        )

    with st.expander(
        "Inspect all returned meetings"
    ):

        st.json(meetings)

    st.stop()


# ============================================================
# SELECT MEETING
# ============================================================

meeting = st.selectbox(
    "Select Racecourse",
    filtered,
    format_func=meeting_name,
)

st.success(
    f"Selected racecourse: "
    f"{meeting_name(meeting)}"
)


# ============================================================
# SELECT RACE
# ============================================================

races = get_items(
    meeting,
    "races"
)

if not races:

    st.warning(
        "No races were found in the meeting response."
    )

    with st.expander(
        "Inspect selected meeting"
    ):

        st.json(meeting)

    st.stop()


race = st.selectbox(
    "Select Race",
    races,
    format_func=race_label,
)


# ============================================================
# RACE IDENTIFIERS
# ============================================================

track_slug = get_value(
    meeting,
    "slug",
    "trackSlug",
    default=(
        meeting_name(meeting)
        .lower()
        .replace(" ", "-")
    ),
)

race_number = get_value(
    race,
    "raceNumber",
    "number",
)


# ============================================================
# ANALYSE RACE
# ============================================================

st.header("📊 Race Analysis")

if st.button(
    "Analyse Race",
    type="primary",
):

    try:

        with st.spinner(
            "Analysing race form..."
        ):

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

            runners = get_items(
                race_data,
                "runners"
            )

    except requests.RequestException as error:

        st.error(
            f"Unable to retrieve race form: {error}"
        )

        st.stop()

    except ValueError as error:

        st.error(
            f"Invalid race response: {error}"
        )

        st.stop()


    # ========================================================
    # VALIDATE RUNNERS
    # ========================================================

    if not runners:

        st.warning(
            "No runners returned for this race."
        )

        with st.expander(
            "Inspect race API response"
        ):

            st.json(race_data)

        st.stop()


    # ========================================================
    # SCORE RUNNERS
    # ========================================================

    results = []

    for runner in runners:

        if not isinstance(runner, dict):
            continue

        if runner.get("scratched") is True:
            continue

        try:

            score = score_runner(runner)

        except (TypeError, ValueError, AttributeError):

            score = None

        results.append({

            "Horse": get_value(
                runner,
                "name",
                "runnerName",
                "horseName",
                default="Unknown",
            ),

            "Form Score": score,

            "Recent Form": runner.get(
                "form",
                ""
            ),

            "Barrier": runner.get(
                "barrier"
            ),

            "Weight": runner.get(
                "weight"
            ),

            "Jockey": runner.get(
                "jockey"
            ),

            "Trainer": runner.get(
                "trainer"
            ),

        })


    if not results:

        st.warning(
            "No eligible runners were found."
        )

        st.stop()


    # ========================================================
    # BUILD RANKINGS
    # ========================================================

    df = pd.DataFrame(results)

    df = df.sort_values(
        "Form Score",
        ascending=False,
        na_position="last",
    ).reset_index(drop=True)

    df.insert(
        0,
        "Rank",
        range(1, len(df) + 1)
    )


    # ========================================================
    # RACE INFORMATION
    # ========================================================

    st.subheader(
        f"🏇 {meeting_name(meeting)} "
        f"— Race {race_number}"
    )

    st.write(
        "Date:",
        selected_date.strftime("%d %B %Y")
    )

    st.write(
        "Runners analysed:",
        len(df)
    )


    # ========================================================
    # BEST CHANCE
    # ========================================================

    rated = df.dropna(
        subset=["Form Score"]
    )

    if not rated.empty:

        top_runner = rated.iloc[0]

        st.subheader(
            "🏆 Best Chance — Model Selection"
        )

        st.success(
            f"🥇 {top_runner['Horse']}"
        )

        col1, col2, col3 = st.columns(3)

        with col1:

            st.metric(
                "Model Rank",
                "#1"
            )

        with col2:

            st.metric(
                "Form Score",
                f"{top_runner['Form Score']:.2f}"
            )

        with col3:

            st.metric(
                "Barrier",
                str(top_runner["Barrier"])
            )

        st.caption(
            "Best Chance is the highest-ranked "
            "runner according to the independent "
            "form-scoring model. This is not a "
            "calibrated winning probability."
        )

    else:

        st.warning(
            "No runners have sufficient data "
            "to calculate a form score."
        )


    # ========================================================
    # FULL RACE RANKINGS
    # ========================================================

    st.subheader(
        "📊 Full Race Rankings"
    )

    st.dataframe(
        df,
        hide_index=True,
        use_container_width=True,
        height=500,
    )


    # ========================================================
    # TOP FIVE
    # ========================================================

    st.subheader(
        "⭐ Top Five Model Selections"
    )

    top_five = df.head(5)

    st.dataframe(
        top_five,
        hide_index=True,
        use_container_width=True,
    )


    # ========================================================
    # RANKING EXPLANATION
    # ========================================================

    with st.expander(
        "How are the rankings calculated?"
    ):

        st.write(
            "The model uses the following "
            "independent form-based factors:"
        )

        weights_df = pd.DataFrame({

            "Factor": [
                "Recent Form",
                "Career Performance",
                "Track Performance",
                "Distance Performance",
                "Track & Distance",
                "Barrier & Weight",
            ],

            "Weight": [
                "35%",
                "15%",
                "15%",
                "15%",
                "10%",
                "10%",
            ],

        })

        st.dataframe(
            weights_df,
            hide_index=True,
            use_container_width=True,
        )

        st.info(
            "Bookmaker odds are not included "
            "in the ranking algorithm."
        )


    # ========================================================
    # HISTORICAL ANALYSIS WARNING
    # ========================================================

    if selected_date < date.today():

        st.warning(
            "Historical analysis: The API may "
            "return statistics updated after "
            "the selected race. These rankings "
            "must not be treated as verified "
            "pre-race predictions without "
            "point-in-time data validation."
        )


    # ========================================================
    # RAW RACE DATA
    # ========================================================

    with st.expander(
        "🔍 Inspect Raw Race Data"
    ):

        st.json(race_data)


# ============================================================
# FOOTER
# ============================================================

st.divider()

st.caption(
    "Horse Racing Predictor v0.2 | "
    "Independent Form Rankings | "
    "Australian Racing | "
    "Experimental Model"
)
