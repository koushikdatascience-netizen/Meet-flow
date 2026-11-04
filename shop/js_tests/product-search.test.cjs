const { test } = require('node:test');
const assert = require('node:assert/strict');
const { rank } = require('../static/shop/product-search.js');
const products = [
  {id: 1, name: 'Whole chicken', stock: '5'},
  {id: 2, name: 'Chicken breast', stock: '5'},
  {id: 3, name: 'Chicken', stock: '5'},
  {id: 4, name: 'Chicken drumsticks', stock: '0'},
  {id: 5, name: 'Fish fillet', stock: '5'},
];
test('exact and prefix matches precede words later in the name', () => {
  assert.deepEqual(rank(products, 'chicken').map(p => p.id), [3, 2, 1, 4]);
});
test('multi-word partial search supports words in either order', () => {
  assert.deepEqual(rank(products, 'bre chi').map(p => p.id), [2]);
});
test('search ignores case, accents and repeated whitespace', () => {
  assert.deepEqual(rank(products, '  CHÍCKEN   BRE  ').map(p => p.id), [2]);
});
test('no match produces no choices', () => {
  assert.deepEqual(rank(products, 'mutton'), []);
});
test('purchasing lets an out-of-stock exact match appear first', () => {
  const items = [{id: 1, name: 'Egg', stock: 0}, {id: 2, name: 'Egg tray', stock: 5}];
  assert.deepEqual(rank(items, 'egg', true).map(p => p.id), [1, 2]);
  assert.deepEqual(rank(items, 'egg').map(p => p.id), [2, 1]);
});
