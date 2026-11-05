# -*- coding: utf-8 -*-
"""Synthetic demo data (not measurement data) for trying the app and for the presets."""
import io, csv
import numpy as np


def _csv(header, rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue().encode('utf-8')


def spectra():
    rng = np.random.default_rng(1)
    x = np.round(np.arange(20, 50.001, 0.02), 2)
    peaks = [(27.9, 1.0), (29.7, 0.55), (33.6, 0.45), (35.6, 0.12), (39.9, 0.2), (45.3, 0.15)]
    cols = {}
    for name, w, s in (('5vol_750C', 0.12, 1.0), ('5vol_950C', 0.13, 0.95), ('5vol_1050C', 0.15, 0.9)):
        y = sum(a * s * 1000 / (1 + ((x - c) / w) ** 2) for c, a in peaks) + 40 + rng.normal(0, 6, len(x))
        cols[name] = np.round(y, 1)
    rows = [[f'{xi:.2f}'] + [f'{cols[k][i]:.1f}' for k in cols] for i, xi in enumerate(x)]
    return _csv(['two_theta_deg'] + list(cols), rows)


def ticks():
    rows = [['beta-Yb2Si2O7', v] for v in (27.9, 29.7, 33.6, 39.9, 45.3)] + [['beta-SiC', v] for v in (35.6, 41.4)]
    return _csv(['phase', 'two_theta_deg'], rows)


def bars():
    rng = np.random.default_rng(2)
    rows = []
    for region, n_ind, mu in (('SiC-free', 7, 0.10), ('10 vol% SiC', 4, 0.53)):
        for i in range(1, n_ind + 1):
            m = rng.normal(mu, 0.08)
            for c in range(1, 5):
                rows.append([region, f'{region[:3]}-{i}', f'L{c}', f'{max(-0.05, rng.normal(m, 0.06)):.3f}'])
    return _csv(['region', 'indent', 'crack', 'h'], rows)


def scatter():
    rng = np.random.default_rng(3)
    rows = []
    for T, A, lam in ((750, 1, 0.07), (950, 12, 0.13), (1050, 14, 0.19)):
        for P, l0 in ((9.8, 36), (19.6, 62), (49, 116), (98, 196)):
            for k in range(12):
                li = rng.normal(l0, 0.06 * l0)
                dl = max(0, A + lam * li + rng.normal(0, 4))
                rows.append([T, P, f'{li:.1f}', f'{dl / li:.3f}'])
    return _csv(['T_C', 'P_N', 'l_i_um', 'h'], rows)


def twin():
    rng = np.random.default_rng(4)
    rows = []
    for comp, base in (('5', 8.4), ('10', 8.8)):
        for T, k in ((750, 2.2), (850, 4.3), (950, 5.5), (1050, 6.4), (1150, 4.8)):
            for i in range(10):
                rows.append([comp, T, f'{comp}-{T}-{i + 1}', f'{rng.normal(base - 0.001 * (T - 750), 0.4):.2f}', f'{rng.normal(k + (0.3 if comp == "10" else 0), 0.3):.2f}'])
    return _csv(['composition', 'T_C', 'indent', 'Hv_GPa', 'K_MPam05'], rows)


def _one_file(seed, base, slope, ns):
    """One CSV per composition: column A = x (set temperature), column B = y (one measurement per row)."""
    rng = np.random.default_rng(seed)
    rows = []
    for T, n in zip((750, 850, 950, 1050, 1150), ns):
        mu = base - slope * (T - 700)
        for _ in range(n):
            rows.append([T, f'{max(0.5, rng.normal(mu, 0.07 * mu + 1.0)):.1f}'])
    return _csv(['Temperature (°C)', 'Crack length (µm)'], rows)


def five_vol():
    return _one_file(5, 70.0, 0.05, (8, 8, 10, 9, 8))


def ten_vol():
    return _one_file(6, 60.0, 0.08, (9, 10, 8, 12, 10))


def closure():
    """One row per corner crack: temperature, load, indentation, corner, crack length before and after annealing.
    x = initial crack length (a measured value), y = how much the crack closed; means per load."""
    rng = np.random.default_rng(7)
    rows = []
    mean_dl = {750: (3, 5, 9, 20), 950: (16, 14, 30, 37), 1050: (21, 36, 48, 46)}
    for T, dls in mean_dl.items():
        for P, l0, dl0 in zip((9.8, 19.6, 49, 98), (33, 65, 121, 192), dls):
            for i in range(1, 4):
                li_ind = rng.normal(l0, 0.05 * l0)            # the indentation sets the crack size
                dl_ind = max(0.0, rng.normal(dl0, 0.25 * dl0 + 1.5))
                for c in range(1, 5):
                    li = rng.normal(li_ind, 0.05 * l0)
                    dl = min(li, max(0.0, rng.normal(dl_ind, 0.35 * dl0 + 2.0)))
                    rows.append([T, P, i, c, f'{li:.1f}', f'{li - dl:.1f}'])
    return _csv(['temperature_C', 'load_N', 'indent', 'corner', 'l_before_um', 'l_after_um'], rows)


def sieve():
    """Sieve fractions (mass %) after firing at three holding temperatures; each temperature adds up to 100 %."""
    vals = {1200: (52.4, 31.8, 15.8), 1300: (38.6, 35.1, 26.3), 1400: (21.7, 33.9, 44.4)}
    rows = [[T, c, f'{v:.1f}'] for T, vs in vals.items() for c, v in zip(('<38 um', '38-63 um', '>63 um'), vs)]
    return _csv(['Holding temperature (degC)', 'Particle size', 'Mass fraction (%)'], rows)     # ASCII だけ（Excel でも崩れない）


DEMOS = {'demo_spectra.csv': spectra, 'demo_ticks.csv': ticks, 'demo_bars.csv': bars, 'demo_scatter.csv': scatter, 'demo_twin.csv': twin,
         'demo_5vol.csv': five_vol, 'demo_10vol.csv': ten_vol, 'demo_closure.csv': closure, 'demo_sieve.csv': sieve}
