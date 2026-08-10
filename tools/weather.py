"""
Weather tool using Open-Meteo - free, no API key required.
Gives structured forecast data rather than scraped search snippets.
"""
import requests

from tools.base import Tool


class WeatherTool(Tool):
    name = "get_weather"
    description = (
        "Get current weather and a short forecast for a city. "
        "Prefer this over web_search for weather questions - the data is structured and reliable."
    )
    input_schema = {
        "type": "object",
        "properties": {"city": {"type": "string", "description": "City name, e.g. 'Bengaluru'"}},
        "required": ["city"],
    }

    def run(self, city: str) -> str:
        try:
            # Step 1: turn the city name into coordinates
            geo = requests.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": city, "count": 1},
                timeout=10,
            )
            geo.raise_for_status()
            results = geo.json().get("results")
            if not results:
                return f"Could not find a location named '{city}'."

            place = results[0]
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