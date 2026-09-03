import numpy as np

class PositionSizer:
    def __init__(self, bal, risk=2.0):
        self.bal, self.risk = bal, risk / 100

    def calc(self, entry, stop, conf=50, liq=0):
        ra = self.bal * self.risk
        ru = abs(entry - stop)
        if ru == 0: return 0
        s = (ra / ru) * (0.5 + conf/100)
        if liq > 0: s *= min(1.0, liq/100000)
        else: s *= 0.5
        return min(s * entry, self.bal * 0.15)

class RiskManager:
    def __init__(self, bal, risk=2.0, max_pos=5):
        self.bal, self.risk, self.max_pos = bal, risk, max_pos
        self.sizer = PositionSizer(bal, risk)

    def calculate_position_size(self, entry, stop, z=0, liq=0):
        conf = min(100, max(0, z*20+50))
        return self.sizer.calc(entry, stop, conf, liq)

    def calculate_risk_reward(self, entry, stop, target):
        r, w = abs(entry-stop), abs(target-entry)
        return w/r if r > 0 else 0

    def should_take_trade(self, score, n_pos):
        return n_pos < self.max_pos and score >= 60