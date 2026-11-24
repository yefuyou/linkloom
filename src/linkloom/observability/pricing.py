"""Token rates used only to estimate the cost of recorded usage for observation.

They never limit, reserve or reject anything: telemetry multiplies the usage a Provider reported by these rates.
"""
INPUT_USD_PER_MILLION = 1.0
OUTPUT_USD_PER_MILLION = 5.0
