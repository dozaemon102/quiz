document.addEventListener("keydown", function (e) {
  if (e.key === "1") selectByIndex(0);
  if (e.key === "2") selectByIndex(1);
  if (e.key === "3") selectByIndex(2);
  if (e.key === "4") selectByIndex(3);

  if (e.key === "Enter") {
    const next = document.getElementById("next-link");
    if (next && next.href) {
      e.preventDefault();
      window.location.href = next.href;
      return;
    }

    const checked = document.querySelector('input[name="answer"]:checked');
    const form = document.querySelector("form");
    if (checked && form) {
      e.preventDefault();
      if (typeof form.requestSubmit === "function") form.requestSubmit();
      else form.submit();
    }
  }
});

function selectByIndex(i) {
  const radios = Array.from(document.querySelectorAll('input[name="answer"]'));
  const r = radios[i];
  if (r) r.checked = true;
}