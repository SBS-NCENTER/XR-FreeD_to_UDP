<script>
  export let history = []
  $: max = Math.max(60, ...history)
  $: pts = history.map((v,i)=>`${(i/Math.max(1,history.length-1))*100},${100-(v/max)*100}`).join(' ')
</script>

<svg class="spark" viewBox="0 0 100 100" preserveAspectRatio="none">
  <polyline points={pts} fill="none" stroke="var(--accent)" stroke-width="2" vector-effect="non-scaling-stroke"/>
</svg>
