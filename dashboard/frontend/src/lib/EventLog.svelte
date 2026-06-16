<script>export let log = []</script>

<div class="log">
  {#each log as e}<div class={e.k}>[{e.t}] {e.m}</div>{/each}
</div>
