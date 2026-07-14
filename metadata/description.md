# Kanyō — description

A production computer-vision pipeline that watches wildlife cameras around the clock and turns raw video into detections, clips, and notifications.

Kanyō (観鷹, "contemplating falcons") streams from YouTube or any RTSP/HTTP source and runs YOLOv8 bird detection behind a debounced state machine that suppresses false positives; it records arrival and departure clips, sends Telegram notifications, and feeds a companion web viewer for review. It has run continuously in production since early 2026, monitoring peregrine falcons at Harvard's Memorial Hall, developed through an academic partnership with Nobel laureate economist Claudia Goldin and Harvard FAS. It is also the existence proof of the Ho System: the methodology's question — can a practitioner with strong systems thinking but no formal engineering background ship production AI? — needed an answer that was not a position paper. The answer was six weeks, 114 tests, and a species-agnostic architecture deployed where a peregrine falcon is not a benchmark and Harvard FAS is not a test environment. The system is real, and it works.

Python and FastAPI with YOLOv8 for detection and a React + Tailwind frontend, packaged in Docker across NVIDIA, Intel, and CPU-only hardware profiles.
