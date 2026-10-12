import pytest

from app.models import CompressionOptions, FormatInfo
from app.services.compression_planner import (
    MB, Evaluation, Perf, PlanError, PlannerConfig, QueueSnapshot, SourceVideo, TargetSpec,
    build_alternatives, check_admission, effective_duration, estimate_seconds, evaluate,
    min_target_bytes, normalize_codec, pick_audio_kbps, plan_encode, queue_wait_seconds,
    scale_dims, select_source, total_kbps,
)

CFG = PlannerConfig()
PERF = Perf(throughput=50e6, two_pass_factor=1.6)


def src(w=1920, h=1080, fps=30, duration=60.0, codec="h264", audio=True, size=None):
    return SourceVideo(duration=duration, width=w, height=h, fps=fps, vcodec=codec,
                       has_audio=audio, size_bytes=size)


def target(mb=20, **kw):
    return TargetSpec(target_bytes=int(mb * MB), **kw)


def fmt(fid, res, vcodec="avc1.640028", acodec="none", fps=30, tbr=None, filesize=None, ext="mp4", abr=None):
    has_video = vcodec != "none"
    return FormatInfo(format_id=fid, ext=ext, resolution=res, fps=fps, vcodec=vcodec, acodec=acodec,
                      tbr=tbr, filesize=filesize, abr=abr, has_video=has_video,
                      has_audio=acodec != "none")


# ---------------------------------------------------------------- bitrate budget

def test_total_bitrate_formula():
    # 20 MB over 60 s minus 6% overhead
    assert total_kbps(20 * MB, 60, CFG) == pytest.approx(20e6 * 8 * 0.94 / 60 / 1000)


def test_plan_bitrates_add_up_to_budget():
    p = plan_encode(src(), target(20), "veryfast", PERF, CFG)
    assert p.audio_kbps == 128
    assert p.video_kbps + p.audio_kbps <= total_kbps(20 * MB, 60, CFG)
    # what the encoder is asked for fits the target with the reserve intact
    assert (p.video_kbps + p.audio_kbps) * 1000 * 60 / 8 <= 20 * MB * (1 - CFG.container_overhead)


def test_audio_bitrate_drops_on_small_budget():
    assert pick_audio_kbps(2500, True, None, CFG) == 128
    assert pick_audio_kbps(250, True, None, CFG) == 48
    assert pick_audio_kbps(50, True, None, CFG) == 32  # floor


def test_audio_capped_by_preset():
    assert pick_audio_kbps(5000, True, 64, CFG) == 64


def test_no_audio_track_gives_whole_budget_to_video():
    p = plan_encode(src(audio=False), target(20), "veryfast", PERF, CFG)
    assert p.audio_kbps == 0
    assert p.video_kbps == int(total_kbps(20 * MB, 60, CFG))


# ---------------------------------------------------------------- resolution ladder

def test_ladder_steps_down_until_bpp_is_sufficient():
    # ~2.4 Mbps of video: 1080p30 is 0.038 bpp (< 0.05), 720p30 is 0.086
    p = plan_encode(src(), target(20), "veryfast", PERF, CFG)
    assert (p.width, p.height, p.fps) == (1280, 720, 30)
    assert p.bpp >= CFG.min_bpp


def test_high_fps_is_halved_before_resolution_drops():
    p = plan_encode(src(fps=60), target(60), "veryfast", PERF, CFG)
    # 60 MB/60 s ~ 7.4 Mbps: 1080p60 is 0.06 bpp -> keeps 60 fps
    assert (p.height, p.fps) == (1080, 60)
    p = plan_encode(src(fps=60), target(35), "veryfast", PERF, CFG)
    assert (p.height, p.fps) == (1080, 30)


def test_never_upscales():
    p = plan_encode(src(w=854, h=480), target(100), "veryfast", PERF, CFG)
    assert (p.width, p.height) == (854, 480)


def test_non_ladder_source_height_is_kept():
    p = plan_encode(src(w=720, h=406), target(100), "veryfast", PERF, CFG)
    assert p.height == 406


def test_vertical_video_uses_short_side():
    p = plan_encode(src(w=1080, h=1920), target(20), "veryfast", PERF, CFG)
    assert (p.width, p.height) == (720, 1280)


def test_max_height_cap():
    p = plan_encode(src(), target(500, max_height=480), "veryfast", PERF, CFG)
    assert p.height == 480


def test_scale_dims_are_even():
    w, h = scale_dims(1918, 1078, 361)
    assert w % 2 == 0 and h % 2 == 0


def test_very_long_video_into_small_size_is_rejected_with_minimum():
    with pytest.raises(PlanError) as e:
        plan_encode(src(duration=1200), target(10), "veryfast", PERF, CFG)
    assert e.value.code == "budget_too_low"
    need = e.value.extra["min_target_mb"]
    assert need > 10
    # and the stated minimum really is enough
    plan_encode(src(duration=1200), target(need), "veryfast", PERF, CFG)


def test_min_target_bytes_is_lowest_rung():
    need = min_target_bytes(100, src(), CFG)
    p = plan_encode(src(duration=100), TargetSpec(target_bytes=need + 1000), "veryfast", PERF, CFG)
    assert p.height == 144


def test_very_short_video():
    p = plan_encode(src(duration=0.5), target(10), "veryfast", PERF, CFG)
    assert p.height == 1080 and p.est_seconds < 5


# ---------------------------------------------------------------- duration edge cases

def test_zero_duration():
    with pytest.raises(PlanError) as e:
        plan_encode(src(duration=0), target(), "veryfast", PERF, CFG)
    assert e.value.code == "zero_duration"


def test_unknown_duration_live_stream():
    with pytest.raises(PlanError) as e:
        plan_encode(src(duration=None), target(), "veryfast", PERF, CFG)
    assert e.value.code == "unknown_duration"


def test_trim_changes_effective_duration():
    assert effective_duration(100, 10, 40) == 30
    assert effective_duration(100, None, 500) == 100
    with pytest.raises(PlanError):
        effective_duration(100, 100, None)
    with pytest.raises(PlanError):
        effective_duration(100, 50, 40)


def test_max_duration_applies_after_trim():
    with pytest.raises(PlanError) as e:
        plan_encode(src(duration=3600), target(500), "veryfast", PERF, CFG)
    assert e.value.code == "too_long"
    p = plan_encode(src(duration=3600), target(500, trim_start=100, trim_end=700), "veryfast", PERF, CFG)
    assert p.duration == 600


# ---------------------------------------------------------------- cost estimate

def test_estimate_scales_with_duration_codec_and_preset():
    base = estimate_seconds(60, src(), 1280, 720, 30, "veryfast", PERF, CFG)
    assert estimate_seconds(120, src(), 1280, 720, 30, "veryfast", PERF, CFG) == pytest.approx(base * 2)
    assert estimate_seconds(60, src(codec="av1"), 1280, 720, 30, "veryfast", PERF, CFG) > \
        estimate_seconds(60, src(codec="vp9"), 1280, 720, 30, "veryfast", PERF, CFG) > base
    assert estimate_seconds(60, src(), 1280, 720, 30, "faster", PERF, CFG) > base
    two = Perf(throughput=PERF.throughput, two_pass_factor=2.0)
    assert estimate_seconds(60, src(), 1280, 720, 30, "veryfast", two, CFG) == pytest.approx(base / 1.6 * 2.0)


def test_estimate_value():
    # 60 s * (30 fps * 1280*720 * 1.0 + 30 * 1920*1080 * 0.15) / 50e6 * 1.6
    expected = 60 * (30 * 1280 * 720 + 30 * 1920 * 1080 * 0.15) / 50e6 * 1.6
    assert estimate_seconds(60, src(), 1280, 720, 30, "veryfast", PERF, CFG) == pytest.approx(expected)


def test_normalize_codec():
    assert normalize_codec("avc1.64001F") == "h264"
    assert normalize_codec("vp09.00.40.08") == "vp9"
    assert normalize_codec("av01.0.08M.08") == "av1"
    assert normalize_codec("hvc1") == "hevc"
    assert normalize_codec(None) == "unknown"


# ---------------------------------------------------------------- admission

def plan_for(seconds=100.0):
    p = plan_encode(src(), target(20), "veryfast", PERF, CFG)
    from dataclasses import replace
    return replace(p, est_seconds=seconds)


def test_admission_ok_when_idle():
    ev = check_admission(plan_for(100), src(), QueueSnapshot(), CFG)
    assert ev.ok and ev.queue_wait == 0
    assert ev.total_seconds == pytest.approx(100 * CFG.safety_factor)


def test_admission_formula_queue_wait_plus_estimate():
    # queue: 2 jobs of 400 s -> wait 1400 s with sf 1.75; own 100*1.75 = 175 -> 1575 <= 1800
    snap = QueueSnapshot(remaining_seconds=[400, 400], waiting_count=1)
    assert queue_wait_seconds([400, 400], CFG) == pytest.approx(1400)
    assert check_admission(plan_for(100), src(), snap, CFG).ok
    # own 300*1.75 = 525 -> 1925 > 1800
    ev = check_admission(plan_for(300), src(), snap, CFG)
    assert not ev.ok and ev.code == "queue_busy"


def test_deferred_skips_queue_wait_but_not_own_cost():
    snap = QueueSnapshot(remaining_seconds=[2000], waiting_count=1)
    assert check_admission(plan_for(100), src(), snap, CFG, deferred=True).ok
    ev = check_admission(plan_for(1100), src(), QueueSnapshot(), CFG, deferred=True)
    assert ev.code == "too_slow"


def test_concurrency_divides_queue_wait():
    cfg = PlannerConfig(concurrency=2)
    assert queue_wait_seconds([400, 400], cfg) == pytest.approx(700)


@pytest.mark.parametrize("snap,code", [
    (QueueSnapshot(waiting_count=5), "queue_full"),
    (QueueSnapshot(user_jobs=1), "user_limit"),
    (QueueSnapshot(ip_jobs=2), "ip_limit"),
    (QueueSnapshot(free_disk_bytes=2 * 1024**3), "disk_low"),
    (QueueSnapshot(ram_capacity_mb=500), "ram_low"),
])
def test_admission_limits(snap, code):
    ev = check_admission(plan_for(10), src(size=100 * MB), snap, CFG)
    assert not ev.ok and ev.code == code


def test_input_size_limit():
    ev = check_admission(plan_for(10), src(size=2000 * MB), QueueSnapshot(), CFG)
    assert ev.code == "input_too_large"


def test_disk_check_counts_reservations():
    snap = QueueSnapshot(free_disk_bytes=5 * 1024**3)
    assert check_admission(plan_for(10), src(size=500 * MB), snap, CFG).ok
    snap = QueueSnapshot(free_disk_bytes=5 * 1024**3, reserved_disk_bytes=2 * 1024**3)
    assert check_admission(plan_for(10), src(size=500 * MB), snap, CFG).code == "disk_low"


# ---------------------------------------------------------------- source selection

FORMATS = [
    fmt("140", None, vcodec="none", acodec="mp4a.40.2", ext="m4a", abr=128, filesize=1 * MB),
    fmt("251", None, vcodec="none", acodec="opus", ext="webm", abr=140, filesize=1 * MB),
    fmt("137", "1920x1080", tbr=4000, filesize=30 * MB),
    fmt("248", "1920x1080", vcodec="vp9", ext="webm", tbr=3000, filesize=25 * MB),
    fmt("136", "1280x720", tbr=2000, filesize=15 * MB),
    fmt("247", "1280x720", vcodec="vp9", ext="webm", tbr=1500),
    fmt("398", "1280x720", vcodec="av01.0.05M.08", tbr=1200),
    fmt("135", "854x480", tbr=1000, filesize=8 * MB),
    fmt("sb0", None, vcodec="none", ext="mhtml"),
]


def test_select_source_prefers_h264_at_same_height_and_caps():
    s = select_source(FORMATS, 60, 720)
    assert s.format_spec == "136+140" and s.vcodec == "h264" and s.height == 720
    assert s.size_bytes == 16 * MB


def test_select_source_prefer_h264_over_height():
    formats = [fmt("247", "1280x720", vcodec="vp9"), fmt("135", "854x480"),
               fmt("140", None, vcodec="none", acodec="mp4a", ext="m4a")]
    assert select_source(formats, 60, 720).format_spec == "247+140"
    assert select_source(formats, 60, 720, prefer_h264=True).format_spec == "135+140"


def test_select_source_progressive_format_needs_no_audio():
    s = select_source([fmt("18", "640x360", acodec="mp4a")], 30, 1080)
    assert s.format_spec == "18" and s.has_audio


def test_select_source_smallest_when_nothing_under_cap():
    s = select_source([fmt("137", "1920x1080"), fmt("136", "1280x720")], 30, 480)
    assert s.height == 720


def test_size_from_tbr_when_filesize_missing():
    s = select_source([fmt("22", "1280x720", acodec="mp4a", tbr=1600)], 100, 1080)
    assert s.size_bytes == 1600 * 1000 / 8 * 100


# ---------------------------------------------------------------- evaluate + alternatives

def run_eval(options: CompressionOptions, snap=QueueSnapshot(), duration=600, formats=FORMATS,
             cfg=CFG, perf=PERF, quality=None):
    t = TargetSpec(target_bytes=int((options.target_size_mb or 20) * MB), max_height=options.max_height,
                   resolution=options.resolution,
                   encoder_preset=options.encoder_preset, trim_start=options.trim_start,
                   trim_end=options.trim_end)
    return evaluate(formats, duration, quality, t, options.prefer_h264, options.deferred, snap, perf, cfg)


def test_evaluate_downloads_only_what_the_encode_keeps():
    ev = run_eval(CompressionOptions(target_size_mb=20), duration=600)
    assert ev.ok
    # 20 MB / 10 min ~ 200 kbps of video -> 240p at most; downloads the 480p H.264 stream
    assert ev.plan.height <= 240
    assert ev.source.height == 480 and ev.source.vcodec == "h264"


def test_evaluate_uses_upgrade_preset_when_it_fits():
    ev = run_eval(CompressionOptions(target_size_mb=50), duration=60)
    assert ev.ok and ev.plan.encoder_preset == "faster"


def test_evaluate_falls_back_to_base_preset():
    # veryfast needs >= ~5.9 Mpx/s here, faster >= ~9 Mpx/s
    slow = Perf(throughput=7e6, two_pass_factor=1.6)
    ev = run_eval(CompressionOptions(target_size_mb=50), duration=120, perf=slow)
    base = run_eval(CompressionOptions(target_size_mb=50, encoder_preset="veryfast"), duration=120, perf=slow)
    assert base.ok and ev.ok and ev.plan.encoder_preset == "veryfast"


def test_evaluate_rejects_unknown_preset():
    ev = run_eval(CompressionOptions(target_size_mb=20, encoder_preset="placebo"))
    assert ev.code == "invalid_preset"


def test_evaluate_without_formats_assumes_worst_case():
    ev = run_eval(CompressionOptions(target_size_mb=20), formats=[], duration=60)
    assert ev.ok and not ev.source.dims_known and ev.source.height == 1080


def _alternatives(options, ev, **kw):
    def run(d):
        return run_eval(CompressionOptions(**d), **kw)
    return build_alternatives(options.model_dump(), ev, run, CFG, kw.get("duration", 600), True)


def test_alternatives_for_busy_queue_are_ordered_and_all_pass():
    # own cost fits alone (~1200 s) but not behind 900 s * 1.75 of queue
    busy = QueueSnapshot(remaining_seconds=[900], waiting_count=1)
    slow = Perf(throughput=50e6, two_pass_factor=1.6)
    opts = CompressionOptions(target_size_mb=200, prefer_h264=False)
    ev = run_eval(opts, snap=busy, perf=slow, duration=300)
    assert ev.code == "queue_busy"
    alts = _alternatives(opts, ev, snap=busy, perf=slow, duration=300)
    kinds = [a["kind"] for a in alts]
    assert kinds[-1] == "deferred"
    order = ["h264", "resolution", "preset", "trim", "deferred"]
    assert kinds == sorted(kinds, key=order.index)
    for a in alts:
        assert run_eval(CompressionOptions(**a["options"]), snap=busy, perf=slow, duration=300).ok


def test_alternative_for_too_long_is_trim():
    opts = CompressionOptions(target_size_mb=100)
    ev = run_eval(opts, duration=3600)
    assert ev.code == "too_long"
    alts = _alternatives(opts, ev, duration=3600)
    assert [a["kind"] for a in alts] == ["trim"]
    assert alts[0]["options"]["trim_end"] == CFG.max_input_duration


def test_too_long_and_too_slow_combines_trim_with_other_alternatives():
    # trimmed to 20 min it is still too slow at 720p -> alternatives on top of the trim
    opts = CompressionOptions(target_size_mb=500)
    ev = run_eval(opts, duration=3600)
    alts = _alternatives(opts, ev, duration=3600)
    assert alts
    for a in alts:
        assert a["options"]["trim_end"] <= CFG.max_input_duration
        assert a["label"].startswith("Trim to")
        assert run_eval(CompressionOptions(**a["options"]), duration=3600).ok


def test_alternatives_for_tiny_budget():
    opts = CompressionOptions(target_size_mb=1)
    ev = run_eval(opts, duration=1200 - 1)
    assert ev.code == "budget_too_low" and ev.min_target_mb
    alts = _alternatives(opts, ev, duration=1200 - 1)
    assert alts[0]["kind"] == "bigger_target"
    assert alts[0]["options"]["target_size_mb"] == ev.min_target_mb
    assert any(a["kind"] == "trim" for a in alts)


def test_no_alternatives_for_unknown_duration():
    opts = CompressionOptions(target_size_mb=20)
    ev = run_eval(opts, duration=None)
    assert ev.code == "unknown_duration"
    assert _alternatives(opts, ev, duration=None) == []


def test_summary_shape():
    ev = run_eval(CompressionOptions(target_size_mb=20), duration=60)
    s = ev.summary()
    assert s["admitted"] and {"width", "height", "estimate_seconds", "total_seconds", "source"} <= s.keys()
    assert isinstance(Evaluation(ok=False).summary(), dict)


def test_select_source_follows_ytdlp_order_for_ties():
    # yt-dlp lists formats worst -> best: https above HLS, plain audio above DRC
    formats = [
        fmt("140-drc", None, vcodec="none", acodec="mp4a.40.2", ext="m4a", abr=129.5),
        fmt("140", None, vcodec="none", acodec="mp4a.40.2", ext="m4a", abr=129.4),
        fmt("229", "426x240", vcodec="avc1.4D4015", tbr=310),  # m3u8
        fmt("133", "426x240", vcodec="avc1.4D4015", tbr=150),  # https
        fmt("242", "426x240", vcodec="vp9"),
    ]
    assert select_source(formats, 600, 240).format_spec == "133+140"


# ---------------------------------------------------------------- explicit resolution

def test_explicit_resolution_is_kept_when_it_fits():
    p = plan_encode(src(), target(20, resolution=480), "veryfast", PERF, CFG)
    assert (p.width, p.height) == (854, 480)


def test_explicit_resolution_may_drop_fps_but_not_size():
    # 480p60 needs ~1.2 Mbps at min bpp; 480p30 ~0.6 Mbps
    p = plan_encode(src(fps=60), target(7, resolution=480), "veryfast", PERF, CFG)
    assert (p.height, p.fps) == (480, 30)


def test_explicit_resolution_above_source_uses_source():
    p = plan_encode(src(w=640, h=360), target(100, resolution=720), "veryfast", PERF, CFG)
    assert p.height == 360


def test_explicit_resolution_over_budget_is_rejected_not_lowered():
    with pytest.raises(PlanError) as e:
        plan_encode(src(duration=635), target(2, resolution=480), "veryfast", PERF, CFG)
    assert e.value.code == "resolution_budget"
    need = e.value.extra["min_target_mb"]
    # the stated minimum really gives 480p
    assert plan_encode(src(duration=635), target(need, resolution=480), "veryfast", PERF, CFG).height == 480


def test_resolution_budget_alternatives_keep_resolution_or_go_auto():
    # 10 MB over 10:35 fits 144p automatically, but not the requested 480p
    opts = CompressionOptions(target_size_mb=10, resolution=480)
    ev = run_eval(opts, duration=635)
    assert ev.code == "resolution_budget"
    alts = _alternatives(opts, ev, duration=635)
    assert [a["kind"] for a in alts] == ["bigger_target", "trim", "auto_resolution"]
    for a in alts:
        r = run_eval(CompressionOptions(**a["options"]), duration=635)
        assert r.ok
        if a["kind"] != "auto_resolution":
            assert r.plan.height == 480
    assert alts[0]["label"].startswith(f"Use {ev.min_target_mb:g} MB for 480p")
    assert alts[2]["options"]["resolution"] is None and "Auto resolution (144p)" in alts[2]["label"]


def test_slow_resolution_alternative_sets_explicit_resolution():
    busy = QueueSnapshot(remaining_seconds=[900], waiting_count=1)
    opts = CompressionOptions(target_size_mb=200)
    ev = run_eval(opts, snap=busy, duration=300)
    res = [a for a in _alternatives(opts, ev, snap=busy, duration=300) if a["kind"] == "resolution"]
    assert res and res[0]["options"]["resolution"] < ev.plan.short_side
