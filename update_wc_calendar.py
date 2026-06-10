#!/usr/bin/env python3
"""
WM 2026 Kalender-Updater

Liest eine bestehende ICS-Datei, lädt Matchdaten als JSON und ersetzt SUMMARY/LOCATION/
DESCRIPTION der bestehenden EVENTs. Die Events werden über ihre Reihenfolge im Kalender
Match 1..104 zugeordnet. Dadurch bleiben UIDs stabil und abonnierte/importierte Kalender
können Events aktualisieren statt Dubletten zu erzeugen.

Datenquelle Standard: football-data.org über FOOTBALL_DATA_TOKEN.
OpenFootball kann weiterhin als Fallback mit --provider openfootball genutzt werden.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Any

DEFAULT_SOURCE_URL = "https://raw.githubusercontent.com/openfootball/worldcup.json/master/2026/worldcup.json"
FOOTBALL_DATA_BASE_URL = "https://api.football-data.org/v4"
DEFAULT_INPUT_ICS = "FIFA_WM_2026_Alle_Spiele.ics"
DEFAULT_OUTPUT_ICS = "FIFA_WM_2026_Alle_Spiele_updated.ics"
def fetch_football_data_matches(api_key: str, season: int = 2026) -> Dict[str, Any]:
    if not api_key:
        raise SystemExit(
            "Für --provider football-data brauchst du einen API-Key. "
            "Setze ihn mit --api-key oder als Umgebungsvariable FOOTBALL_DATA_TOKEN."
        )

    query = urllib.parse.urlencode({"season": season})
    url = f"{FOOTBALL_DATA_BASE_URL}/competitions/WC/matches?{query}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "wm2026-calendar-updater/1.0",
            "X-Auth-Token": api_key,
        },
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        data = json.loads(response.read().decode(charset))

    matches = data.get("matches")
    if not isinstance(matches, list):
        raise SystemExit("football-data.org hat kein JSON-Feld 'matches' als Liste geliefert.")

    return {"matches": [convert_football_data_match(match) for match in matches]}
def _football_data_team_name(team: Any) -> str:
    if isinstance(team, dict):
        return team.get("shortName") or team.get("name") or team.get("tla") or "TBD"
    return normalize_team(team)


def _football_data_score(match: Dict[str, Any]) -> Dict[str, Any] | None:
    score = match.get("score")
    if not isinstance(score, dict):
        return None

    for key in ("fullTime", "regularTime"):
        value = score.get(key)
        if isinstance(value, dict):
            home = value.get("home")
            away = value.get("away")
            if home is not None and away is not None:
                return {"home": home, "away": away}

    return None


def _football_data_goals(match: Dict[str, Any]) -> List[Dict[str, Any]]:
    for key in ("goals", "events", "incidents", "timeline"):
        value = match.get(key)
        if isinstance(value, list):
            return value
    return []


def convert_football_data_match(match: Dict[str, Any]) -> Dict[str, Any]:
    converted: Dict[str, Any] = {
        "team1": _football_data_team_name(match.get("homeTeam")),
        "team2": _football_data_team_name(match.get("awayTeam")),
        "round": match.get("stage") or match.get("group") or "Spiel",
        "status": match.get("status") or "",
        "date": match.get("utcDate") or "",
    }

    score = _football_data_score(match)
    if score is not None:
        converted["score"] = score

    goals = _football_data_goals(match)
    if goals:
        converted["events"] = goals

    return converted

STAGE_DE = {
    "Matchday": "Gruppenspiel",
    "Round of 32": "Sechzehntelfinale",
    "Round of 16": "Achtelfinale",
    "Quarter-finals": "Viertelfinale",
    "Quarter-final": "Viertelfinale",
    "Semi-finals": "Halbfinale",
    "Semi-final": "Halbfinale",
    "Third-place": "Spiel um Platz 3",
    "Third Place": "Spiel um Platz 3",
    "Final": "Finale",
}

# Optional: ergänzt Stadien, falls eine Datenquelle nur Städte liefert.
# Kannst du erweitern/überschreiben, ohne die Logik anzufassen.
GROUND_TO_STADIUM = {
    "Mexico City": "Estadio Azteca, Mexico City, Mexico",
    "Guadalajara (Zapopan)": "Estadio Akron, Zapopan / Guadalajara, Mexico",
    "Monterrey (Guadalupe)": "Estadio BBVA, Guadalupe / Monterrey, Mexico",
    "Toronto": "BMO Field, Toronto, Canada",
    "Vancouver": "BC Place, Vancouver, Canada",
    "Atlanta": "Mercedes-Benz Stadium, Atlanta, USA",
    "Boston": "Gillette Stadium, Foxborough / Boston, USA",
    "Dallas": "AT&T Stadium, Arlington / Dallas, USA",
    "Houston": "NRG Stadium, Houston, USA",
    "Kansas City": "Arrowhead Stadium, Kansas City, USA",
    "Los Angeles": "SoFi Stadium, Inglewood / Los Angeles, USA",
    "Miami": "Hard Rock Stadium, Miami Gardens / Miami, USA",
    "New York/New Jersey (East Rutherford)": "MetLife Stadium, East Rutherford / New York-New Jersey, USA",
    "Philadelphia": "Lincoln Financial Field, Philadelphia, USA",
    "San Francisco Bay Area (Santa Clara)": "Levi’s Stadium, Santa Clara / San Francisco Bay Area, USA",
    "Seattle": "Lumen Field, Seattle, USA",
}

PLACEHOLDER_RE = re.compile(r"^(W|L|R|Runner-up|Winner|2nd|1st|Best)", re.IGNORECASE)


def fetch_json(url: str) -> Dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "wm2026-calendar-updater/1.0"})
    with urllib.request.urlopen(req, timeout=30) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return json.loads(response.read().decode(charset))


def unfold_ics(text: str) -> List[str]:
    raw = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines: List[str] = []
    for line in raw:
        if line.startswith((" ", "\t")) and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return lines


def fold_ics_line(line: str, limit: int = 74) -> str:
    # RFC5545: lines should be folded. This implementation keeps UTF-8 bytes intact.
    b = line.encode("utf-8")
    if len(b) <= limit:
        return line
    out = []
    current = ""
    for ch in line:
        if len((current + ch).encode("utf-8")) > limit:
            out.append(current)
            current = " " + ch
            limit = 73
        else:
            current += ch
    out.append(current)
    return "\r\n".join(out)


def escape_ics(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def parse_events(lines: List[str]) -> List[List[str]]:
    events = []
    current = None
    for line in lines:
        if line == "BEGIN:VEVENT":
            current = [line]
        elif line == "END:VEVENT" and current is not None:
            current.append(line)
            events.append(current)
            current = None
        elif current is not None:
            current.append(line)
    return events


def replace_property(event_lines: List[str], name: str, value: str) -> List[str]:
    escaped = escape_ics(value)
    prop = f"{name}:{escaped}"
    replaced = False
    out = []
    for line in event_lines:
        if line.startswith(name + ":") or line.startswith(name + ";"):
            if not replaced:
                out.append(prop)
                replaced = True
            continue
        if line == "END:VEVENT" and not replaced:
            out.append(prop)
            replaced = True
        out.append(line)
    return out


def round_de(round_name: str) -> str:
    for key, val in STAGE_DE.items():
        if round_name.startswith(key):
            return val
    return round_name


def normalize_team(name: Any) -> str:
    if name is None:
        return "TBD"
    if isinstance(name, dict):
        name = name.get("name") or name.get("title") or name.get("code") or "TBD"
    name = str(name).strip()
    m = re.fullmatch(r"W(\d+)", name)
    if m:
        return f"Sieger Spiel {m.group(1)}"
    m = re.fullmatch(r"L(\d+)", name)
    if m:
        return f"Verlierer Spiel {m.group(1)}"
    return name


def is_known_pairing(team1: str, team2: str) -> bool:
    return not (
        PLACEHOLDER_RE.match(team1 or "")
        or PLACEHOLDER_RE.match(team2 or "")
        or re.fullmatch(r"[WL]\d+", team1 or "")
        or re.fullmatch(r"[WL]\d+", team2 or "")
    )


def _score_pair_from_value(value: Any) -> tuple[Any, Any] | None:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return value[0], value[1]
    if isinstance(value, str):
        m = re.search(r"(\d+)\s*[-:]\s*(\d+)", value)
        if m:
            return m.group(1), m.group(2)
    return None


def extract_score(match: Dict[str, Any]) -> tuple[int, int] | None:
    for key in ("score", "result", "final_score", "fulltime", "ft"):
        value = match.get(key)
        pair = _score_pair_from_value(value)
        if pair is not None:
            return int(pair[0]), int(pair[1])
        if isinstance(value, dict):
            for nested_key in ("ft", "fulltime", "regular", "score", "result"):
                pair = _score_pair_from_value(value.get(nested_key))
                if pair is not None:
                    return int(pair[0]), int(pair[1])
            home = value.get("home") or value.get("team1") or value.get("h")
            away = value.get("away") or value.get("team2") or value.get("a")
            if home is not None and away is not None:
                return int(home), int(away)

    direct_pairs = (
        ("score1", "score2"),
        ("goals1", "goals2"),
        ("team1_score", "team2_score"),
        ("team1_goals", "team2_goals"),
        ("home_score", "away_score"),
        ("home_goals", "away_goals"),
    )
    for left_key, right_key in direct_pairs:
        if match.get(left_key) is not None and match.get(right_key) is not None:
            return int(match[left_key]), int(match[right_key])

    return None


def is_match_finished(match: Dict[str, Any], score: tuple[int, int] | None) -> bool:
    status = str(match.get("status") or match.get("state") or match.get("phase") or "").lower()
    finished_words = ("finished", "final", "full-time", "full time", "ft", "played", "complete", "completed")
    if any(word in status for word in finished_words):
        return score is not None

    date_value = match.get("date") or match.get("datetime") or match.get("kickoff") or match.get("time")
    if score is not None and date_value:
        try:
            parsed = datetime.fromisoformat(str(date_value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed < datetime.now(timezone.utc)
        except ValueError:
            pass

    return False


def _goal_minute(goal: Dict[str, Any]) -> str:
    minute = goal.get("minute") or goal.get("time") or goal.get("matchMinute") or goal.get("mins")
    stoppage = goal.get("stoppage") or goal.get("stoppageTime") or goal.get("extra")
    if minute is None:
        return ""
    minute_text = str(minute).strip().rstrip("'")
    if stoppage:
        return f"{minute_text}+{str(stoppage).strip()}’"
    return f"{minute_text}’"


def _goal_scorer(goal: Dict[str, Any]) -> str:
    scorer = goal.get("scorer") or goal.get("player") or goal.get("playerName") or goal.get("name")
    if isinstance(scorer, dict):
        scorer = scorer.get("name") or scorer.get("fullName") or scorer.get("displayName")
    return str(scorer or "Unbekannt").strip()


def _goal_team(goal: Dict[str, Any], team1: str, team2: str) -> str:
    team = goal.get("team") or goal.get("side") or goal.get("for") or goal.get("teamName")
    if isinstance(team, dict):
        team = team.get("name") or team.get("title") or team.get("code")
    team_text = str(team or "").strip()
    if team_text in ("1", "team1", "home", "h"):
        return team1
    if team_text in ("2", "team2", "away", "a"):
        return team2
    return team_text


def _normalise_goal(goal: Any, team1: str, team2: str, default_team: str = "") -> Dict[str, str] | None:
    if not isinstance(goal, dict):
        return None
    minute = _goal_minute(goal)
    scorer = _goal_scorer(goal)
    team = _goal_team(goal, team1, team2) or default_team
    own_goal = bool(goal.get("ownGoal") or goal.get("own_goal") or goal.get("og"))
    penalty = bool(goal.get("penalty") or goal.get("isPenalty"))
    tags = []
    if penalty:
        tags.append("Elfmeter")
    if own_goal:
        tags.append("Eigentor")
    suffix = f" ({', '.join(tags)})" if tags else ""
    prefix = f"{minute} " if minute else ""
    team_part = f" ({team})" if team else ""
    return {"text": f"{prefix}{scorer}{team_part}{suffix}".strip(), "minute": minute or "999"}


def extract_goals(match: Dict[str, Any], team1: str, team2: str) -> List[str]:
    goals: List[Dict[str, str]] = []

    for key in ("goals", "scorers", "goalScorers"):
        value = match.get(key)
        if isinstance(value, list):
            for goal in value:
                normalised = _normalise_goal(goal, team1, team2)
                if normalised:
                    goals.append(normalised)

    paired_goal_lists = (
        ("goals1", team1),
        ("goals2", team2),
        ("team1_goals", team1),
        ("team2_goals", team2),
        ("home_goals", team1),
        ("away_goals", team2),
    )
    for key, default_team in paired_goal_lists:
        value = match.get(key)
        if isinstance(value, list):
            for goal in value:
                normalised = _normalise_goal(goal, team1, team2, default_team=default_team)
                if normalised:
                    goals.append(normalised)

    for key in ("events", "incidents", "timeline"):
        value = match.get(key)
        if isinstance(value, list):
            for event in value:
                if not isinstance(event, dict):
                    continue
                event_type = str(event.get("type") or event.get("eventType") or "").lower()
                if "goal" not in event_type and not event.get("scorer"):
                    continue
                normalised = _normalise_goal(event, team1, team2)
                if normalised:
                    goals.append(normalised)

    def sort_key(goal: Dict[str, str]) -> int:
        minute = re.match(r"\d+", goal["minute"])
        return int(minute.group(0)) if minute else 999

    deduped = []
    seen = set()
    for goal in sorted(goals, key=sort_key):
        if goal["text"] not in seen:
            seen.add(goal["text"])
            deduped.append(goal["text"])
    return deduped


def event_values(match_number: int, match: Dict[str, Any]) -> Dict[str, str]:
    team1_raw = match.get("team1") or match.get("home_team") or match.get("home") or "TBD"
    team2_raw = match.get("team2") or match.get("away_team") or match.get("away") or "TBD"
    team1 = normalize_team(team1_raw)
    team2 = normalize_team(team2_raw)
    stage = round_de(str(match.get("round") or match.get("stage") or "Spiel"))
    location = str(match.get("venue") or match.get("stadium") or match.get("ground") or "").strip()
    location = GROUND_TO_STADIUM.get(location, location)

    score = extract_score(match)
    finished = is_match_finished(match, score)
    known_pairing = is_known_pairing(str(team1_raw), str(team2_raw))

    if finished and score is not None:
        summary = f"{team1} {score[0]}:{score[1]} {team2}"
    elif known_pairing:
        summary = f"{team1} vs {team2}"
    else:
        summary = f"{stage}: {team1} vs {team2}"

    desc_parts = [
        f"FIFA WM 2026, Spiel {match_number}.",
        f"Runde: {stage}.",
        f"Paarung aus Datenquelle: {team1} vs {team2}.",
    ]

    if finished and score is not None:
        desc_parts.append(f"Endstand: {team1} {score[0]}:{score[1]} {team2}.")
        goals = extract_goals(match, team1, team2)
        if goals:
            desc_parts.append("Torschützen:\n" + "\n".join(f"- {goal}" for goal in goals))
        else:
            desc_parts.append("Torschützen: In der Datenquelle nicht vorhanden.")
    elif score is not None:
        desc_parts.append(f"Aktueller/übernommener Spielstand: {team1} {score[0]}:{score[1]} {team2}.")

    desc_parts.extend([
        f"Ort: {location}.",
        f"Automatisch aktualisiert: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}.",
    ])
    desc = " ".join(desc_parts)
    return {"SUMMARY": summary, "LOCATION": location, "DESCRIPTION": desc}


def update_ics(input_path: Path, output_path: Path, matches: List[Dict[str, Any]]) -> int:
    lines = unfold_ics(input_path.read_text(encoding="utf-8"))
    events = parse_events(lines)
    if len(events) < len(matches):
        print(f"Warnung: ICS hat nur {len(events)} Events, Datenquelle hat {len(matches)} Matches.", file=sys.stderr)

    updated_events: List[List[str]] = []
    for idx, event in enumerate(events):
        if idx < len(matches):
            values = event_values(idx + 1, matches[idx])
            event = replace_property(event, "SUMMARY", values["SUMMARY"])
            event = replace_property(event, "LOCATION", values["LOCATION"])
            event = replace_property(event, "DESCRIPTION", values["DESCRIPTION"])
            event = replace_property(event, "LAST-MODIFIED", datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
            event = replace_property(event, "SEQUENCE", str(int(datetime.now(timezone.utc).timestamp())))
        updated_events.append(event)

    # Kalender neu zusammensetzen, Eventblöcke ersetzen.
    out_lines = []
    event_iter = iter(updated_events)
    inside = False
    for line in lines:
        if line == "BEGIN:VEVENT":
            out_lines.extend(next(event_iter))
            inside = True
        elif line == "END:VEVENT" and inside:
            inside = False
            continue
        elif inside:
            continue
        else:
            out_lines.append(line)

    output_path.write_text("\r\n".join(fold_ics_line(l) for l in out_lines if l != "") + "\r\n", encoding="utf-8")
    return min(len(events), len(matches))


def main() -> int:
    parser = argparse.ArgumentParser(description="Aktualisiert eine WM-2026-ICS-Datei mit aktuellen Spielpaarungen.")
    parser.add_argument("--input", default=DEFAULT_INPUT_ICS, help="Bestehende ICS-Datei")
    parser.add_argument("--output", default=DEFAULT_OUTPUT_ICS, help="Zieldatei")
    parser.add_argument("--source", default=DEFAULT_SOURCE_URL, help="JSON-Datenquelle mit Feld 'matches'")
    parser.add_argument(
        "--provider",
        choices=("openfootball", "football-data"),
        default="football-data",
        help="Datenanbieter: openfootball ohne Key oder football-data.org mit API-Key",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("FOOTBALL_DATA_TOKEN", ""),
        help="API-Key für football-data.org. Alternativ Umgebungsvariable FOOTBALL_DATA_TOKEN setzen.",
    )
    parser.add_argument("--season", type=int, default=2026, help="Saison/Jahr für football-data.org")
    args = parser.parse_args()

    if args.provider == "football-data":
        data = fetch_football_data_matches(args.api_key, args.season)
    else:
        data = fetch_json(args.source)

    matches = data.get("matches")
    if not isinstance(matches, list):
        raise SystemExit("Die Datenquelle enthält kein JSON-Feld 'matches' als Liste.")

    count = update_ics(Path(args.input), Path(args.output), matches)
    print(f"OK: {count} Kalendertermine aktualisiert -> {args.output}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
