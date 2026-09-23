import type { BreakoutEventFull, BreakoutSignal, BreakoutStatusFull } from '../../src/api/types';

declare const signal: BreakoutSignal;
declare const event: BreakoutEventFull;
declare const status: BreakoutStatusFull;

// @ts-expect-error A missing quote must not be treated as a number.
const signalPrice: number = signal.price;
// @ts-expect-error The current price can be absent from a live event.
event.current_price.toFixed(2);
// @ts-expect-error A missing score cannot be formatted as a measured zero.
event.intrinsic_strength_score.toFixed(1);
// @ts-expect-error A partial opening-range anchor has no support zone.
event.support_zone.low;
// @ts-expect-error Unknown worker health is distinct from healthy.
const workerHealthy: boolean = status.worker.healthy;

if (event.current_price !== null) event.current_price.toFixed(2);
if (event.support_zone !== null) event.support_zone.low.toFixed(2);
void signalPrice;
void workerHealthy;
