Bug fix request: allocating the same order line twice must be idempotent.

Repository: cosmicpython/code (allocation service). Code lives under src/allocation/,
tests under tests/.

Observed behaviour: when the same Allocate command (same orderid, sku and qty)
is handled twice, for example because the message broker delivered it twice,
the service allocates it again. The second call emits a second Allocated
event, so the allocations read model (views.allocations) gets a duplicate row
and the event is published twice. If the first batch is now full, the line is
even reserved a second time in another batch, wasting stock.

Expected behaviour (business rule): an order line is allocated at most once.
If Product.allocate receives a line that is already allocated to one of the
product's batches, it must return that batch's reference and do nothing else:
no new allocation, no new event, no version change. Allocating a different
order line must keep working exactly as before.

Where to change: the rule belongs in the domain model, in Product.allocate in
src/allocation/domain/model.py. A Batch keeps its allocated lines in the set
batch._allocations. Do not change tests or files outside src/allocation/.

When the change is done, run the "regression" check to confirm existing tests
still pass, then reply with final.

Implementation hint from the reviewer (manual help): at the very start of
Product.allocate, before the `try:` line, add a loop over self.batches; if
`line in batch._allocations`, return batch.reference immediately. Leave the
rest of the method unchanged. Use edit_file with old set to the two lines
"    def allocate(self, line: OrderLine) -> str:" and "        try:" copied exactly.
