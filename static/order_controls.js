/* Cancellation requires its own account-bound review and a deliberate click. */
(() => {
  const dialog = document.getElementById("cancel-order-dialog");
  if (!dialog) return;
  let review = null, trigger = null, generation = 0;
  const $ = id => document.getElementById(id);
  async function post(path, body) {
    const response = await fetch(path, {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const data = await response.json();
    if (!response.ok || !data.ok) throw Error(data.error || "Broker request failed");
    return data;
  }
  const close = () => { generation++; dialog.classList.add("hidden"); review = null; trigger?.focus(); };
  document.addEventListener("click", async e => {
    const button = e.target.closest("[data-cancel-review]");
    if (!button || button.disabled) return;
    const opened = ++generation;
    button.disabled = true; trigger = button; review = null;
    dialog.classList.remove("hidden"); $("cancel-order-message").textContent = "Verifying the working order and account…";
    $("cancel-order-submit").disabled = true; $("cancel-order-ack").value = "";
    $("cancel-order-close").focus();
    try {
      const response = await post(`/api/signals/${encodeURIComponent(button.dataset.cancelReview)}/cancel-review`, {});
      if (opened !== generation || dialog.classList.contains("hidden")) return;
      review = {...response, signalId:button.dataset.cancelReview};
      $("cancel-order-ack").value = ""; $("cancel-order-submit").disabled = true;
      $("cancel-order-message").textContent = `${review.identity.paper_mode ? "BROKER PAPER" : "LIVE"} account …${String(review.identity.account_id).slice(-4)} · ${review.ticker} · ${review.order_id}. ${review.note} Type ${review.ticker} to cancel the remaining quantity.`;
      $("cancel-order-ack").focus();
    } catch (error) { if (opened === generation) $("cancel-order-message").textContent=error.message; }
    finally {button.disabled=false;}
  });
  $("cancel-order-ack").addEventListener("input", e => { $("cancel-order-submit").disabled = !review || e.target.value.trim().toUpperCase() !== review.ticker; });
  $("cancel-order-submit").addEventListener("click", async e => {
    if (!review || $("cancel-order-ack").value.trim().toUpperCase() !== review.ticker) return;
    e.currentTarget.disabled = true;
    const ticket = review, submitted = generation; review = null;
    try {
      const result = await post(`/api/signals/${encodeURIComponent(ticket.signalId)}/cancel`, {review_token:ticket.review_token,ack_ticker:ticket.ticker});
      if (submitted === generation) $("cancel-order-message").textContent = `${result.message}. ${result.status.error || ""} To replace an order, first verify cancellation, then obtain a fresh idea and review. No replacement is sent automatically.`;
      window.dispatchEvent(new Event("desk:refresh"));
    } catch(error) { if (submitted === generation) $("cancel-order-message").textContent=`${error.message}. Refresh and reconcile before trying again.`; }
  });
  $("cancel-order-close").addEventListener("click",close);
  document.addEventListener("keydown",e=>{
    if(dialog.classList.contains("hidden"))return;
    if(e.key==="Escape"){e.preventDefault();close();}
    if(e.key!=="Tab")return;
    const inputs=[...dialog.querySelectorAll("input,button:not([disabled])")];
    const first=inputs[0],last=inputs.at(-1);
    if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}
    else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}
  });
})();
