"""Equalizer presets, effect presets and the audio-state shape shared by the dashboard and the Discord filter commands.

Pure: takes/returns plain dicts (wavelink ``Filter.payload`` mappings), no discord or wavelink imports.
"""

from __future__ import annotations

from typing import Any

__all__ = ('EFFECTS', 'EQ_PRESETS', 'audio_state', 'match_eq_preset')

BANDS = 15

EQ_PRESETS: dict[str, list[float]] = {
    'flat': [0.0] * BANDS,
    'bassboost': [0.2, 0.15, 0.1, 0.05, 0.0, -0.05, -0.1, -0.1, -0.1, -0.1, -0.1, -0.1, -0.1, -0.1, -0.1],
    'treble': [-0.1, -0.1, -0.1, -0.05, 0.0, 0.05, 0.1, 0.12, 0.15, 0.18, 0.2, 0.22, 0.24, 0.25, 0.25],
    'vocal': [-0.1, -0.05, 0.0, 0.1, 0.2, 0.25, 0.25, 0.2, 0.15, 0.1, 0.0, -0.05, -0.1, -0.1, -0.1],
    'pop': [-0.05, 0.05, 0.12, 0.15, 0.1, 0.0, -0.05, -0.05, 0.0, 0.05, 0.1, 0.12, 0.12, 0.1, 0.08],
    'rock': [0.18, 0.14, 0.08, 0.02, -0.06, -0.1, -0.04, 0.04, 0.1, 0.15, 0.18, 0.2, 0.2, 0.18, 0.16],
    'electronic': [0.2, 0.17, 0.1, 0.0, -0.08, -0.05, 0.0, 0.05, 0.0, 0.08, 0.14, 0.18, 0.2, 0.18, 0.14],
    'acoustic': [0.12, 0.12, 0.1, 0.06, 0.1, 0.06, 0.04, 0.06, 0.08, 0.1, 0.1, 0.08, 0.08, 0.06, 0.04],
}

# effect name -> (wavelink filter attribute, kwargs for ``.set``). Each effect owns exactly one filter,
# so turning it off is that filter's ``reset()``.
EFFECTS: dict[str, tuple[str, dict[str, Any]]] = {
    'nightcore': ('timescale', {'speed': 1.25, 'pitch': 1.3, 'rate': 1.3}),
    'vaporwave': ('timescale', {'speed': 0.8, 'pitch': 0.8, 'rate': 1.0}),
    'slowed': ('timescale', {'speed': 0.85, 'pitch': 0.9, 'rate': 1.0}),
    '8d': ('rotation', {'rotation_hz': 0.15}),
    'karaoke': ('karaoke', {'level': 1.0, 'mono_level': 1.0, 'filter_band': 220.0, 'filter_width': 100.0}),
    'tremolo': ('tremolo', {'frequency': 4.0, 'depth': 0.5}),
    'vibrato': ('vibrato', {'frequency': 4.0, 'depth': 0.5}),
    'lowpass': ('low_pass', {'smoothing': 20.0}),
    'distortion': ('distortion', {'sin_offset': 0.0, 'sin_scale': 1.0, 'cos_offset': 0.0, 'cos_scale': 1.0,
                                  'tan_offset': 0.0, 'tan_scale': 1.0, 'offset': 0.0, 'scale': 1.2}),
    'mono': ('channel_mix', {'left_to_left': 0.5, 'left_to_right': 0.5, 'right_to_left': 0.5, 'right_to_right': 0.5}),
}

# Timescale effects are told apart by their speed; payload keys are Lavalink's camelCase.
_TIMESCALE_BY_SPEED = {1.25: 'nightcore', 0.8: 'vaporwave', 0.85: 'slowed'}


def match_eq_preset(bands: list[float]) -> str | None:
    """The preset whose gains equal ``bands`` (to rounding), else None."""
    for name, gains in EQ_PRESETS.items():
        if all(abs(a - b) < 0.005 for a, b in zip(bands, gains)):
            return name
    return None


def audio_state(equalizer: dict[int, dict[str, Any]], filters: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Shape wavelink payloads into the ``GET /music/audio`` body (minus ``active``/presets).

    ``filters`` maps filter attribute name (``timescale``, ``rotation``...) to its ``payload``.
    """
    bands = [float(equalizer[i]['gain']) if i in equalizer else 0.0 for i in range(BANDS)]
    ts = filters.get('timescale') or {}
    kar = filters.get('karaoke') or {}
    trem = filters.get('tremolo') or {}
    vib = filters.get('vibrato') or {}
    rot = filters.get('rotation') or {}
    dist = filters.get('distortion') or {}
    cm = filters.get('channel_mix') or {}
    lp = filters.get('low_pass') or {}

    mono = bool(cm) and all(abs(cm.get(k, 0.0) - 0.5) < 0.01 for k in
                            ('leftToLeft', 'leftToRight', 'rightToLeft', 'rightToRight'))
    speed = float(ts.get('speed', 1.0))
    state = {
        'equalizer': {'preset': match_eq_preset(bands) if any(bands) else 'flat', 'bands': bands},
        'filters': {
            'timescale': {'speed': speed, 'pitch': float(ts.get('pitch', 1.0)), 'rate': float(ts.get('rate', 1.0))},
            'karaoke': {'enabled': bool(kar), **kar},
            'tremolo': {'frequency': float(trem.get('frequency', 0.0)), 'depth': float(trem.get('depth', 0.0))},
            'vibrato': {'frequency': float(vib.get('frequency', 0.0)), 'depth': float(vib.get('depth', 0.0))},
            'rotation': {'rotation_hz': float(rot.get('rotationHz', 0.0))},
            'distortion': {'enabled': bool(dist)},
            'channel_mix': {'preset': 'mono' if mono else None},
            'low_pass': {'smoothing': float(lp['smoothing']) if lp.get('smoothing') else None},
        },
    }
    active: list[str] = []
    if ts and (speed != 1.0 or ts.get('pitch', 1.0) != 1.0 or ts.get('rate', 1.0) != 1.0):
        active.append(_TIMESCALE_BY_SPEED.get(round(speed, 2), 'timescale'))
    if rot.get('rotationHz'):
        active.append('8d')
    if kar:
        active.append('karaoke')
    if trem:
        active.append('tremolo')
    if vib:
        active.append('vibrato')
    if lp.get('smoothing'):
        active.append('lowpass')
    if dist:
        active.append('distortion')
    if mono:
        active.append('mono')
    state['effects'] = active
    return state
