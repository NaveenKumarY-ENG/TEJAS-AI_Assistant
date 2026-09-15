"""
Weather tool using Open-Meteo - free, no API key required.
Gives structured forecast data rather than scraped search snippets.
"""
import requests

from tools.base import Tool

# Common Indian cities are frequently referred to by pre-2014-era English
# names that Open-Meteo's geocoding database doesn't recognize as aliases
# at all — confirmed live: "Bangalore" resolves only to an obscure,
# unrelated town in Sindh, Pakistan (no Indian candidate is returned even
# asking for 10 results), and "Bombay"/"Madras" don't resolve to
# Mumbai/Chennai either. This app introduces itself as an Indian
# assistant, so these are exactly the city names most likely to come up in
# real use — and the failure mode isn't a visible error, it's confidently
# wrong-country weather.
_CITY_ALIASES = {
    "bangalore": "Bengaluru",
    "bombay": "Mumbai",
    "madras": "Chennai",
    "calcutta": "Kolkata",
    "poona": "Pune",
    "cochin": "Kochi",
    "trivandrum": "Thiruvananthapuram",
    "mysore": "Mysuru",
    "baroda": "Vadodara",
    # Confirmed live as a real, separate gap: "Leh Ladakh" — the standard,
    # extremely common way people refer to this town (Ladakh being the
    # district/region it's the capital of, routinely tacked on) — returns
    # ZERO results from Open-Meteo's geocoding at all, even though the
    # bare "Leh" resolves fine on its own. "Ladhak" is included as a
    # common real-world misspelling (confirmed live, the user's own
    # spelling) since this tool has no separate typo-tolerance layer.
    "leh ladakh": "Leh",
    "leh ladhak": "Leh",
    "ladakh": "Leh",
    "ladhak": "Leh",
}


class WeatherTool(Tool):
    name = "get_weather"
    description = "Get current weather + short forecast for a city. Prefer over web_search for weather — more reliable, structured data."
    input_schema = {
        "type": "object",
        "properties": {"city": {"type": "string", "description": "City name, e.g. 'Bengaluru'"}},
        "required": ["city"],
    }

    def run(self, city: str) -> str:
        try:
            # Step 1: turn the city name into coordinates. count=10 (not 1)
            # plus picking by population below — asking for only the single
            # top result meant blindly trusting Open-Meteo's own ranking,
            # which isn't reliable when an obscure namesake elsewhere shares
            # the name with a real, well-known city (confirmed live: a
            # small town in Pakistan outranked — and for the alias-less
            # spelling, entirely replaced — the actual major city a plain
            # city-name query almost always means).
            query = _CITY_ALIASES.get(city.strip().lower(), city)
            geo = requests.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": query, "count": 10},
                timeout=10,
            )
            geo.raise_for_status()
            results = geo.json().get("results")
            if not results:
                return f"Could not find a location named '{city}'."

            # Prefer an EXACT name match over a fuzzier one, even if the
            # fuzzy match has a much bigger population. Confirmed live as
            # a real, separate bug from the Bangalore/Bombay case above:
            # searching "Leh" (the real town in Ladakh, India; population
            # ~37k) returned "Le Havre" (France; population ~186k) as the
            # single highest-population candidate in Open-Meteo's fuzzy
            # results — even though an exact "Leh, India" match was right
            # there in the same result set, just outranked by population.
            # Population is only used to break ties among exact matches
            # (several real, differently-located places can share one
            # name — several other countries have their own "Leh" too) or
            # as a last resort when nothing matches exactly (a genuine
            # typo/rare-spelling query, where the fuzzy fallback below is
            # still the best available guess).
            exact_matches = [r for r in results if r["name"].strip().lower() == query.strip().lower()]
            candidates = exact_matches or results
            place = max(candidates, key=lambda r: r.get("population") or 0)
            lat, lon = place["latitude"], place["longitude"]
            label = f"{place['name']}, {place.get('country', '')}".strip(", ")

            # Step 2: fetch the forecast
            wx = requests.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,precipitation",
                    "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                    "forecast_days": 3,
                    "timezone": "auto",
                },
                timeout=10,
            )
            wx.raise_for_status()
            data = wx.json()

            cur = data["current"]
            lines = [
                f"Weather for {label}:",
                f"Now: {cur['temperature_2m']}degC, humidity {cur['relative_humidity_2m']}%, "
                f"wind {cur['wind_speed_10m']} km/h, precipitation {cur['precipitation']} mm",
                "",
                "Forecast:",
            ]

            daily = data["daily"]
            for i, date in enumerate(daily["time"]):
                lines.append(
                    f"  {date}: {daily['temperature_2m_min'][i]}-{daily['temperature_2m_max'][i]}degC, "
                    f"{daily['precipitation_probability_max'][i]}% chance of rain"
                )

            return "\n".join(lines)
        except Exception as e:
            return f"Error fetching weather: {e}"