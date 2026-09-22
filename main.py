import os
import json
import swisseph as swe
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from datetime import datetime, timedelta
from typing import List, Dict, Any

app = FastAPI(title="Decoupled Enterprise BTR Engine", version="3.0.0")

class GeoCoordinates(BaseModel):
    latitude: float
    longitude: float
    timezone_offset_hours: float

class CalculationSettings(BaseModel):
    ayanamsa_selection: str

class BirthProfile(BaseModel):
    birth_date: str
    birth_time_24h: str 
    geographic_coordinates: GeoCoordinates
    calculation_settings: CalculationSettings

class DynamicLifeEvent(BaseModel):
    category: str
    exact_date: str
    impact_weight_score: float = 1.0

class DynamicCalibrationPayload(BaseModel):
    birth_profile: BirthProfile
    historical_milestones: List[DynamicLifeEvent]

class DecoupledAstroEngine:
    def __init__(self, rules_file_path: str = "astro_rules.json"):
        self.rules_path = rules_file_path
        self.ayanamsa_map = {"LAHIRI": swe.SE_SIDM_LAHIRI, "PUSHYA_PAKSHA": swe.SE_SIDM_USER, "KP_STELLAR": swe.SE_SIDM_KRISHNAMURTI}
        self.dasha_order = ["Ketu", "Venus", "Sun", "Moon", "Mars", "Rahu", "Jupiter", "Saturn", "Mercury"]
        self.dasha_years = {"Ketu": 7, "Venus": 20, "Sun": 6, "Moon": 10, "Mars": 7, "Rahu": 18, "Jupiter": 16, "Saturn": 19, "Mercury": 17}

    def _load_significator_matrix(self) -> Dict[str, List[str]]:
        if not os.path.exists(self.rules_path):
            return {"MARRIAGE": ["Venus", "Mars", "Saturn"]}
        with open(self.rules_path, "r") as f:
            return json.load(f)

    def _get_julian_day(self, date_str: str, time_str: str, tz_offset: float) -> float:
        dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S")
        return swe.julday(dt.year, dt.month, dt.day, dt.hour + (dt.minute / 60.0) + (dt.second / 3600.0) - tz_offset)

    def execute_decoupled_pipeline(self, profile: BirthProfile, events: List[DynamicLifeEvent]) -> Dict[str, Any]:
        significator_matrix = self._load_significator_matrix()
        jd_ut = self._get_julian_day(profile.birth_date, profile.birth_time_24h, profile.geographic_coordinates.timezone_offset_hours)
        swe.set_sid_mode(self.ayanamsa_map.get(profile.calculation_settings.ayanamsa_selection, swe.SE_SIDM_LAHIRI))
        if profile.calculation_settings.ayanamsa_selection == "PUSHYA_PAKSHA":
            swe.set_sid_mode(swe.SE_SIDM_USER, 0.0, -1.221)

        res, _ = swe.calc_ut(jd_ut, swe.MOON, swe.FLG_SWIEPH | swe.FLG_SIDEREAL)
        moon_long = res

        nakshatra_span = 360.0 / 27.0
        nakshatra_idx = int(moon_long // nakshatra_span)
        fraction_consumed = (moon_long % nakshatra_span) / nakshatra_span
        
        start_lord_idx = nakshatra_idx % 9
        start_lord = self.dasha_order[start_lord_idx]
        years_spent = self.dasha_years[start_lord] * fraction_consumed

        birth_dt = datetime.strptime(f"{profile.birth_date} {profile.birth_time_24h}", "%Y-%m-%d %H:%M:%S")
        pointer = birth_dt - timedelta(days=(years_spent * 365.25))

        timeline = []
        idx = start_lord_idx
        for _ in range(9):
            lord = self.dasha_order[idx]
            m_span = self.dasha_years[lord]
            m_end = pointer + timedelta(days=(m_span * 365.25))

            bhuktis = []
            b_pointer = pointer
            b_idx = self.dasha_order.index(lord)
            for _ in range(9):
                b_lord = self.dasha_order[b_idx]
                b_days = (m_span * self.dasha_years[b_lord] / 120.0) * 365.25
                b_end = b_pointer + timedelta(days=b_days)
                bhuktis.append({"bhukti_lord": b_lord, "start": b_pointer, "end": b_end})
                b_pointer = b_end
                b_idx = (b_idx + 1) % 9

            timeline.append({"mahadasha_lord": lord, "start": pointer, "end": m_end, "bhuktis": bhuktis})
            pointer = m_end
            idx = (idx + 1) % 9

        score_breakdown = []
        earned_sum, total_sum = 0.0, 0.0
        
        for ev in events:
            ev_date = datetime.strptime(ev.exact_date, "%Y-%m-%d")
            total_sum += ev.impact_weight_score
            active_md, active_bh = "Unknown", "Unknown"
            
            for md in timeline:
                if md["start"] <= ev_date <= md["end"]:
                    active_md = md["mahadasha_lord"]
                    for bh in md["bhuktis"]:
                        if bh["start"] <= ev_date <= bh["end"]:
                            active_bh = bh["bhukti_lord"]
            
            valid_indicators = significator_matrix.get(ev.category, [])
            is_match = (active_md in valid_indicators) or (active_bh in valid_indicators)
            
            earned = ev.impact_weight_score if is_match else 0.0
            earned_sum += earned
            
            score_breakdown.append({
                "category": ev.category,
                "dasha_active": f"{active_md}-{active_bh}",
                "match_status": is_match
            })

        conf = (earned_sum / total_sum) * 100 if total_sum > 0 else 0.0
        return {"confidence_rating_percentage": round(conf, 2), "validation_matrix": score_breakdown}

engine = DecoupledAstroEngine()

@app.post("/api/v3/calibrate")
async def calibrate_chart(payload: DynamicCalibrationPayload):
    try:
        results = engine.execute_decoupled_pipeline(payload.birth_profile, payload.historical_milestones)
        return {"status": "success", "data": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
