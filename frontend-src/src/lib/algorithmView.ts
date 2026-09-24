/** Shared generation so default switches actually reread current + history. */

type Listener = () => void;

const listeners = new Set<Listener>();
let generation = 0;

export function getAlgorithmViewGeneration(): number {
  return generation;
}

export function bumpAlgorithmViewGeneration(): number {
  generation += 1;
  for (const listener of listeners) listener();
  return generation;
}

export function subscribeAlgorithmView(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
