# Runes

Svelte 5 introduces runes — a set of powerful primitives for controlling reactivity.

## $state

`$state` creates reactive state.

```svelte
<script>
    let count = $state(0);
</script>

<button onclick={() => count++}>
    clicks: {count}
</button>
```

### Deep reactivity

State is deeply reactive by default — if you mutate a property of an object or push to an array, it will trigger updates.

```svelte
<script>
    let todos = $state([]);

    function addTodo(text) {
        todos.push({ text, done: false });
    }
</script>
```

## $derived

`$derived` creates derived state that automatically updates.

```svelte
<script>
    let count = $state(0);
    let doubled = $derived(count * 2);
</script>
```

## $effect

`$effect` runs side effects when dependencies change.

```svelte
<script>
    let count = $state(0);

    $effect(() => {
        console.log(`count is ${count}`);
    });
</script>
```

!!! warning
    Don't update state inside `$effect` — it can cause infinite loops.
