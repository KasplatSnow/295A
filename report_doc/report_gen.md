You are an expert IEEE technical report formatter and academic writing assistant. Your task is to take the contents from the provided ZIP package and convert them into a polished, submission-ready report update section for VigilZone.

Primary goal:
Format and organize the provided content so it can be directly inserted into the CMPE 295B Final Project Report without the user manually fixing spacing, fonts, headings, figures, references, captions, or section order.

Use the files from the ZIP package as the source material. Do not invent technical claims. Do not add unsupported results. Preserve all project-specific content, but improve formatting, structure, placement, captions, and IEEE-style presentation.

Formatting requirements:
1. Use Times New Roman throughout.
2. Use 12 pt body text unless the existing project report template uses a different size.
3. Use 1.15 or single line spacing consistently with the existing report.
4. Use justified body text where appropriate.
5. Use consistent paragraph spacing: no excessive blank lines, no cramped paragraphs.
6. Use professional academic tone.
7. Keep headings numbered and hierarchical:
   - Chapter X. Title
   - X.1 Section
   - X.1.1 Subsection
8. Do not mix bullet-heavy notes with final report prose unless a checklist/table is explicitly needed.
9. Convert rough bullet points into polished paragraphs where they belong in the report body.
10. Keep tables readable and aligned. Use concise column names and avoid overflowing page width.
11. Use figure placeholders where diagrams or screenshots should be inserted.
12. Every figure placeholder must include a proper IEEE-style caption.
13. Every table must include a proper table title above the table.
14. All references must use IEEE numbered citation style: [1], [2], [3].
15. Citations must appear in the text before the corresponding reference entry.
16. References must be ordered by first appearance in the report section.
17. Do not leave uncited references in the References section.
18. Do not leave citation placeholders such as [REF], [citation needed], or TODO.
19. Do not cite sources that are not used in the text.
20. Preserve the project’s existing terminology: VigilZone, audio-video fusion, BEATs, FastAPI AI service, Django control plane, Redis Streams, PostgreSQL, React dashboard, MediaMTX, evidence capture, notifications, and multi-tenant security.

Important IEEE-style conventions:
1. Figure captions should appear below figures.
   Example:
   Figure 2.3. VigilZone audio-video anomaly detection runtime architecture.
2. Table captions/titles should appear above tables.
   Example:
   Table 2.4. Comparison of video-only, audio-only, and audio-video anomaly detection approaches.
3. Refer to figures and tables in prose before or near where they appear.
   Example:
   “Figure 2.3 shows the updated runtime architecture after the integration of audio-based inference.”
4. Do not write “the below figure.” Use “Figure X.Y shows...”
5. Use numbered citations in IEEE format.
   Example:
   “BEATs is used as the audio backbone because it provides strong pretrained audio representations for AudioSet-style event recognition [4].”
6. Use consistent capitalization for section titles.
7. Avoid first-person language such as “we did” unless the existing report style uses it. Prefer “The system implements...” or “This project contributes...”

Required content organization:
Create or update the report content with the following structure:

1. Abstract update
   - Include market/context motivation.
   - Include the problem: video-only surveillance can miss off-camera or ambiguous events and can produce false positives in noisy/complex environments.
   - Include the solution: VigilZone integrates video inference, BEATs-based audio inference, audio-video fusion, evidence capture, backend incident persistence, and real-time operator notification.
   - Include the contribution: a deployment-oriented multimodal anomaly detection and notification platform, not a claim of inventing a new SOTA model.
   - Include keywords.

2. Introduction update
   Must contain these paragraphs in this order:
   - Market/context paragraph.
   - Problem paragraph.
   - Solution paragraph.
   - Novelty/contribution paragraph.
   - Paper organization paragraph.
   
   The paper organization paragraph should be explicit:
   “The remainder of this report is organized as follows. Chapter 2 presents the updated system architecture...”

3. Literature review / related work
   - Add an IEEE-style related-work section for anomaly detection.
   - Include a table comparing representative research categories:
     video anomaly detection, audio event detection, audio-video anomaly detection, real-time surveillance systems, and notification/incident-management systems.
   - Do not copy the UAV paper’s content. Use its style only as inspiration.
   - Keep the comparison relevant to VigilZone.

4. Updated architecture section
   - Add a figure placeholder for the updated audio-video architecture.
   - Mention the following components:
     React operator dashboard,
     Django control plane,
     FastAPI AI service,
     FFmpeg audio reader,
     video frame readers,
     BEATs audio lane,
     video detection lanes,
     multimodal fusion,
     Redis Streams,
     PostgreSQL,
     notification service,
     evidence storage.
   - Explain trust boundaries: browser does not directly access raw camera/audio streams; backend owns tenant authorization and persistence; AI service owns inference.

5. AI model section
   - Clearly separate:
     video-based inference,
     audio-based inference,
     audio-video fusion,
     normality/uncertainty gating,
     learned fusion shadow mode.
   - Make clear that BEATs is used as a pretrained audio backbone.
   - Make clear that learned fusion is not the default production decision path unless trained and validated.
   - Avoid claiming the project is benchmark-SOTA unless actual benchmark evidence is provided.
   - Use this wording style:
     “The implementation is SOTA-aligned rather than SOTA-claimed.”

6. Data and test results section
   - Add a structured table for test categories:
     video-only test,
     audio-only test,
     audio-video fusion test,
     notification latency,
     evidence generation,
     duplicate event replay,
     tenant isolation,
     frontend notification rendering.
   - Leave clear placeholders for measured values if actual numbers are not available.
   - Mark placeholders professionally:
     “Measured value to be inserted from final demo run.”
   - Do not fabricate metrics.

7. Demo section
   - Add a 5-10 minute demo script.
   - Include “AI audio voice overlay” as a demo narration feature.
   - Structure it by time:
     0:00-1:00 introduction,
     1:00-2:30 dashboard and camera setup,
     2:30-4:00 video-only inference,
     4:00-5:30 audio-only inference,
     5:30-7:00 audio-video fusion,
     7:00-8:30 incident notification and evidence review,
     8:30-10:00 summary and future work.
   - Add placeholders for screenshots or screen recording frames.

8. Novelty section
   Separate novelty into:
   - Project/system novelty.
   - Model/fusion novelty.
   - Deployment/engineering novelty.

   Use careful wording:
   “The novelty is not the invention of BEATs or a new object detector. The novelty is the integration of pretrained audio understanding, real-time video detection, temporal audio-video fusion, evidence management, tenant-scoped backend persistence, and real-time notification delivery into one deployable surveillance workflow.”

9. References
   - Convert all references to IEEE numbered format.
   - Order references by first use.
   - Include references for:
     YOLO or object detection,
     BEATs,
     video anomaly detection,
     audio-video anomaly detection or fusion,
     Django/FastAPI/Redis/PostgreSQL/React only if they are cited in technology sections.
   - Do not leave duplicate numbering.
   - Do not mix APA and IEEE style.

Figure and table placement requirements:
Place these placeholders in the correct sections:

[FIGURE PLACEHOLDER: Updated VigilZone Audio-Video Cloud Deployment Architecture]
Caption:
Figure X.Y. Updated VigilZone cloud deployment architecture with audio-video inference, multimodal fusion, backend incident persistence, and real-time notification delivery.

[FIGURE PLACEHOLDER: AI Audio-Video Runtime Pipeline]
Caption:
Figure X.Y. Audio-video inference pipeline showing video lanes, BEATs audio inference, normality and uncertainty adjustment, multimodal fusion, evidence export, and alert publication.

[FIGURE PLACEHOLDER: End-to-End Incident Sequence]
Caption:
Figure X.Y. End-to-end sequence from camera/audio input to AI detection, multimodal fusion, backend incident persistence, and operator notification.

[FIGURE PLACEHOLDER: Demo Workflow]
Caption:
Figure X.Y. Demonstration workflow for video-only, audio-only, and audio-video anomaly detection scenarios.

Required tables:
1. Table: Comparison of related anomaly detection research and VigilZone.
2. Table: Video-only, audio-only, and audio-video capability comparison.
3. Table: AI model and fusion component responsibilities.
4. Table: Test and evaluation matrix.
5. Table: Demo scenario checklist.

Quality-control checklist before final output:
1. No unsupported SOTA claims.
2. No hallucinated benchmark results.
3. No missing figure captions.
4. No missing table titles.
5. No inconsistent citation numbering.
6. No APA-style references mixed into IEEE references.
7. No raw URLs in body text unless needed in references.
8. No excessive blank lines.
9. No inconsistent font sizes.
10. No unmerged TODOs except clearly marked measurement placeholders.
11. All section titles are professional and report-ready.
12. The final document should read like a polished academic project report, not a set of notes.

Output requirements:
1. Produce a cleaned DOCX version.
2. Produce a cleaned Markdown version.
3. Preserve editable figure placeholders so the user can later insert diagrams.
4. Include a short changelog describing what formatting and structural fixes were made.
5. Do not overwrite the original report unless explicitly instructed.

http://webcam.rhein-taunus-krematorium.de/mjpg/video.mjpg
http://honjin1.miemasu.net/nphMotionJpeg?Resolution=640x480&Quality=Standard