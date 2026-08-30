import numpy as np
rng = np.random.default_rng(7)

N = 200_000      # paths
T = 252          # trading days
dt = 1/252

def sim(sharpe, vol, n=N):
    """GBM with annualized Sharpe and vol. Returns terminal wealth + path stats."""
    mu = sharpe * vol                      # arithmetic drift
    z = rng.standard_normal((n, T))
    # log-return steps
    steps = (mu - 0.5*vol**2)*dt + vol*np.sqrt(dt)*z
    logpath = np.cumsum(steps, axis=1)
    wealth = np.exp(logpath)
    # ruin: touch 10% of starting equity intraday-ish (daily close proxy)
    minw = wealth.min(axis=1)
    final = wealth[:, -1]
    # max drawdown
    runmax = np.maximum.accumulate(wealth, axis=1)
    mdd = (wealth/runmax - 1).min(axis=1)
    return final, minw, mdd

print(f"{'Sharpe':>6} {'AnnVol':>7} {'E[ret]':>8} {'P(>=3x)':>8} {'P(>=4x)':>8} {'P(<50%)':>8} {'P(<10%)':>8} {'medMDD':>8} {'median':>8}")
print("-"*80)
for sharpe in [0.5, 1.0, 1.5, 2.0, 3.0]:
    for vol in [0.30, 0.60, 1.00, 1.50, 2.00]:
        final, minw, mdd = sim(sharpe, vol)
        print(f"{sharpe:>6.1f} {vol*100:>6.0f}% {sharpe*vol*100:>7.0f}% "
              f"{(final>=3).mean()*100:>7.1f}% {(final>=4).mean()*100:>7.1f}% "
              f"{(minw<0.5).mean()*100:>7.1f}% {(minw<0.10).mean()*100:>7.1f}% "
              f"{np.median(mdd)*100:>7.1f}% {np.median(final):>7.2f}x")
    print()
