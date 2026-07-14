# Synthetic chilled-water control sequence

This document describes a fictional HVAC plant created only for software testing.

The chilled-water supply temperature setpoint is 7 degrees Celsius during occupied operation. The expected return-water temperature is approximately 12 degrees Celsius under design conditions.

The primary pump starts before the chiller enable command. The chiller may start only after flow is proven for 30 seconds. When load decreases below 25 percent for ten consecutive minutes, the supervisory controller may evaluate a staged shutdown.

冷冻水供水温度设定值为 7 摄氏度，设计回水温度约为 12 摄氏度。启动制冷机前，必须先启动一次泵，并连续确认水流 30 秒。
