<script>
  import { onMount } from 'svelte'
  let theme = 'dark'
  onMount(() => { theme = localStorage.getItem('xrfd_theme') || 'dark'; document.body.className = theme })
  function toggle(){ theme = theme==='dark'?'light':'dark'; document.body.className = theme; localStorage.setItem('xrfd_theme', theme) }
</script>

<button class="btn-theme" on:click={toggle} title="Monokai / Solarized">&#9789;</button>
