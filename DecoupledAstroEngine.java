package com.astro.engine;

import java.io.BufferedReader;
import java.io.FileReader;
import java.io.File;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.time.temporal.ChronoUnit;
import java.util.*;

public class DecoupledAstroEngine {

    private static final String[] DASHA_ORDER = {"Ketu", "Venus", "Sun", "Moon", "Mars", "Rahu", "Jupiter", "Saturn", "Mercury"};
    private static final int[] DASHA_YEARS = {7, 20, 6, 10, 7, 18, 16, 19, 17};
    private static final double NAKSHATRA_SPAN = 360.0 / 27.0;
    
    private final String rulesFilePath;

    public DecoupledAstroEngine(String rulesFilePath) {
        this.rulesFilePath = rulesFilePath;
    }

    private Map<String, List<String>> loadExternalRules() {
        Map<String, List<String>> matrix = new HashMap<>();
        File file = new File(rulesFilePath);
        if (!file.exists()) {
            matrix.put("MARRIAGE", List.of("Venus", "Mars", "Saturn"));
            return matrix;
        }

        try (BufferedReader br = new BufferedReader(new FileReader(file))) {
            StringBuilder sb = new StringBuilder();
            String line;
            while ((line = br.readLine()) != null) {
                sb.append(line.trim());
            }
            
            String json = sb.toString().replace("{", "").replace("}", "").replace("\"", "");
            String[] pairs = json.split(",");
            for (String pair : pairs) {
                String[] keyValue = pair.split(":");
                String category = keyValue[0].trim();
                String values = keyValue[1].replace("[", "").replace("]", "").trim();
                String[] planets = values.split(",");
                
                List<String> planetList = new ArrayList<>();
                for(String p : planets) {
                    planetList.add(p.trim());
                }
                matrix.put(category, planetList);
            }
        } catch (Exception e) {
            System.err.println("File Parsing Error Encountered: " + e.getMessage());
        }
        return matrix;
    }

    public static class DynamicEvent {
        public String category;
        public String exactDate;
        public double weight;
        public DynamicEvent(String category, String exactDate, double weight) {
            this.category = category; this.exactDate = exactDate; this.weight = weight;
        }
    }

    public static class MahaDasha {
        public String lord;
        public LocalDateTime start;
        public LocalDateTime end;
        public List<Bhukti> bhuktis = new ArrayList<>();
        
        public static class Bhukti {
            public String lord; public LocalDateTime start; public LocalDateTime end;
            public Bhukti(String lord, LocalDateTime start, LocalDateTime end) {
                this.lord = lord; this.start = start; this.end = end;
            }
        }
        public MahaDasha(String lord, LocalDateTime start, LocalDateTime end) {
            this.lord = lord; this.start = start; this.end = end;
        }
    }

    public List<MahaDasha> generateTimeline(LocalDateTime birth, double moonLong) {
        int nakshatraIdx = (int) (moonLong / NAKSHATRA_SPAN);
        double consumedFraction = (moonLong % NAKSHATRA_SPAN) / NAKSHATRA_SPAN;
        int startLordIdx = nakshatraIdx % 9;
        long daysSpent = (long) (DASHA_YEARS[startLordIdx] * consumedFraction * 365.25);

        LocalDateTime pointer = birth.minus(daysSpent, ChronoUnit.DAYS);
        List<MahaDasha> timeline = new ArrayList<>();

        int currentIdx = startLordIdx;
        for (int i = 0; i < 9; i++) {
            String mLord = DASHA_ORDER[currentIdx];
            int mYears = DASHA_YEARS[currentIdx];
            LocalDateTime mEnd = pointer.plus((long)(mYears * 365.25), ChronoUnit.DAYS);

            MahaDasha md = new MahaDasha(mLord, pointer, mEnd);
            LocalDateTime bPointer = pointer;
            int bIdx = currentIdx;
            
            for (int j = 0; j < 9; j++) {
                String bLord = DASHA_ORDER[bIdx];
                double bDays = (mYears * DASHA_YEARS[bIdx] / 120.0) * 365.25;
                LocalDateTime bEnd = bPointer.plus((long) bDays, ChronoUnit.DAYS);
                md.bhuktis.add(new MahaDasha.Bhukti(bLord, bPointer, bEnd));
                bPointer = bEnd;
                bIdx = (bIdx + 1) % 9;
            }
            timeline.add(md);
            pointer = mEnd;
            currentIdx = (currentIdx + 1) % 9;
        }
        return timeline;
    }

    public void evaluateProfile(LocalDateTime birth, double moonLong, List<DynamicEvent> milestones) {
        Map<String, List<String>> currentRulesMatrix = loadExternalRules();
        List<MahaDasha> timeline = generateTimeline(birth, moonLong);
        DateTimeFormatter dtf = DateTimeFormatter.ofPattern("yyyy-MM-dd");

        double totalScore = 0, earnedScore = 0;

        System.out.println("\n=========================================================================");
        System.out.println("DECOUPLED ASSET FILE PARSING LIVE METRICS");
        System.out.println("=========================================================================");

        for (DynamicEvent ev : milestones) {
            LocalDate targetDate = LocalDate.parse(ev.exactDate, dtf);
            totalScore += ev.weight;
            String activeMD = "Unknown", activeBH = "Unknown";

            for (MahaDasha md : timeline) {
                if (!targetDate.isBefore(md.start.toLocalDate()) && !targetDate.isAfter(md.end.toLocalDate())) {
                    activeMD = md.lord;
                    for (MahaDasha.Bhukti bh : md.bhuktis) {
                        if (!targetDate.isBefore(bh.start.toLocalDate()) && !targetDate.isAfter(bh.end.toLocalDate())) {
                            activeBH = bh.lord;
                            break;
                        }
                    }
                    break;
                }
            }

            List<String> validLords = currentRulesMatrix.getOrDefault(ev.category, new ArrayList<>());
            boolean isMatch = validLords.contains(activeMD) || validLords.contains(activeBH);
            if (isMatch) earnedScore += ev.weight;

            System.out.printf("Event: %-20s Active Dasha Window: %-15s Validation: %s\n", 
                    ev.category, (activeMD + "-" + activeBH), (isMatch ? "✅ MATED SUCCESS" : "❌ MISALIGNMENT"));
        }

        System.out.println("-------------------------------------------------------------------------");
        System.out.printf("Dynamic Confidence Score: %.2f%%\n", (earnedScore / totalScore) * 100.0);
        System.out.println("=========================================================================\n");
    }

    public static void main(String[] args) {
        DecoupledAstroEngine engine = new DecoupledAstroEngine("astro_rules.json");
        LocalDateTime birthTime = LocalDateTime.of(1984, 7, 29, 19, 15, 0);
        double calibratedMoonLongitude = 120.0841;

        List<DynamicEvent> userHistory = new ArrayList<>();
        userHistory.add(new DynamicEvent("MARRIAGE", "2012-04-15", 1.0));
        userHistory.add(new DynamicEvent("FIRST_CHILD", "2015-05-27", 1.0));
        userHistory.add(new DynamicEvent("CAREER_DISRUPTION", "2026-04-10", 1.0));

        engine.evaluateProfile(birthTime, calibratedMoonLongitude, userHistory);
    }
}
