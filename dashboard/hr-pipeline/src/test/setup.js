import '@testing-library/jest-dom';
import { afterEach } from 'vitest';
import { cleanup } from '@testing-library/react';

// Auto-unmounts each rendered component tree after every test. Without
// this, render() calls across different tests accumulate multiple
// mounted app instances in the DOM without cleaning up the previous
// ones, which caused test pollution (a real "duplicate key" React
// warning was traced back to this missing cleanup, not an actual bug
// in the app itself).
afterEach(() => {
  cleanup();
});
