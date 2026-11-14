const { test } = require('node:test');
const assert = require('node:assert/strict');
const { matches } = require('../static/inventory/entry-form.js');
const items = [
  { id: '1', name: 'Whole chicken' },
  { id: '2', name: 'Chicken breast' },
  { id: '3', name: 'Chicken' },
  { id: '4', name: 'Fish fillet' },
];

test('initial letters show prefix matches first', () => {
  assert.deepEqual(matches(items, 'chi').map(item => item.id), ['3', '2', '1']);
});
test('exact match precedes longer names', () => {
  assert.deepEqual(matches(items, 'chicken').map(item => item.id), ['3', '2', '1']);
});
test('partial words work regardless of case, accents, whitespace or order', () => {
  assert.deepEqual(matches(items, ' BRE   CHÍ ').map(item => item.id), ['2']);
});
test('empty and unmatched input have no suggestions', () => {
  assert.deepEqual(matches(items, '  '), []);
  assert.deepEqual(matches(items, 'mutton'), []);
});
