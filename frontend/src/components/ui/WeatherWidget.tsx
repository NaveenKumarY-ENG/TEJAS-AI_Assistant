import { useEffect, useState } from "react";
import { WidgetCard } from "./WidgetCard";

const CITY = "Bengaluru";

interface WeatherData {
  city: string;
  temp: number;
  humidity: number;
}

/** Real weather, fetched client-side straight from Open-Meteo (no key needed). */
export function WeatherWidget() {
  const [data, setData] = useState<WeatherData | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const geoRes = await fetch(
          `https://geocoding-api.open-meteo.com/v1/search?name=${encodeURIComponent(CITY)}&count=1`
        );
        const geo = await geoRes.json();
        const place = geo.results?.[0];
        if (!place) throw new Error("no location");

        const wxRes = await fetch(
          `https://api.open-meteo.com/v1/forecast?latitude=${place.latitude}&longitude=${place.longitude}` +
          `&current=temperature_2m,relative_humidity_2m&timezone=auto`
        );
        const wx = await wxRes.json();
        if (cancelled) return;
        setData({ city: place.name, temp: Math.round(wx.current.temperature_2m), humidity: wx.current.relative_humidity_2m });
      } catch {
        if (!cancelled) setError(true);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <WidgetCard title="Weather" tag={data?.city ?? CITY} tagTone="accent">
      {error && <p className="text-[12.5px] text-white/40">Unavailable</p>}
      {!error && !data && <p className="text-[12.5px] text-white/40">Loading…</p>}
      {data && (
        <div className="flex items-baseline gap-2.5">
          <span className="text-[26px] font-semibold tracking-tight">{data.temp}°C</span>
          <span className="text-[12.5px] text-white/45">Humidity {data.humidity}%</span>
        </div>
      )}
    </WidgetCard>
  );
}
