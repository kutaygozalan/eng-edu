import numpy as np
rng = np.random.default_rng(11)
N, T, dt = 200_000, 252, 1/252

def run(true_sharpe, vol):
    mu = true_sharpe*vol
    z = rng.standard_normal((N,T))
    w = np.exp(np.cumsum((mu-0.5*vol**2)*dt + vol*np.sqrt(dt)*z, axis=1))
    return w[:,-1], w.min(axis=1)

print("SCENARIO: you size for a backtested Sharpe of 1.5 (lever to 150% vol = full-Kelly-ish).")
print("But the live Sharpe degrades (as the 2026 literature shows it does).\n")
print(f"{'True live Sharpe':>17} {'P(>=3x)':>9} {'P(lose half)':>13} {'P(lose 90%)':>12} {'median':>8}")
print("-"*64)
for s in [1.5, 1.0, 0.5, 0.25, 0.0, -0.25]:
    f, m = run(s, 1.50)
    print(f"{s:>17.2f} {(f>=3).mean()*100:>8.1f}% {(m<0.5).mean()*100:>12.1f}% {(m<0.1).mean()*100:>11.1f}% {np.median(f):>7.2f}x")

print("\n\nKELLY SIZING for a strategy with 10% standalone vol:")
print(f"{'Sharpe':>7} {'full-Kelly lev':>15} {'implied ann.vol':>16} {'E[growth]':>11} {'half-Kelly ret':>15}")
print("-"*70)
for s in [0.5,1.0,1.5,2.0,3.0]:
    base_vol = 0.10
    f_full = s/base_vol          # optimal leverage multiple
    lev_vol = f_full*base_vol    # = s  -> vol equals Sharpe
    g_full = 0.5*s**2            # expected log growth at full Kelly
    g_half = 0.375*s**2
    print(f"{s:>7.1f} {f_full:>14.1f}x {lev_vol*100:>15.0f}% {(np.exp(g_full)-1)*100:>10.0f}% {(np.exp(g_half)-1)*100:>14.0f}%")
