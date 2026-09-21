/* Live progress for a background job (an agent run or a chat turn).
   Plain vanilla JS, no framework or build step (CLAUDE.md section 5).

   The page POSTs to a /.../start route, gets a job id back, then polls
   GET /jobs/<id> and draws the steps the agent has really taken so far. If
   this file fails to load, the forms still post normally and just wait. */
(function () {
  "use strict";

  var POLL_MS = 700;
  var MAX_POLL_FAILURES = 5;

  function render(block, job) {
    var list = block.querySelector(".progress-steps");
    job.steps.forEach(function (step, i) {
      var li = list.children[i];
      if (!li) {
        li = document.createElement("li");
        li.innerHTML =
          '<span class="progress-icon" aria-hidden="true"></span>' +
          '<span class="progress-text"><span class="progress-label"></span> ' +
          '<span class="progress-detail"></span></span>';
        list.appendChild(li);
      }
      li.className = "progress-step progress-step-" + step.state;
      li.querySelector(".progress-label").textContent = step.label;
      li.querySelector(".progress-detail").textContent = step.detail || "";
    });
    block.querySelector(".progress-thinking").hidden = !job.thinking;
  }

  function poll(block, jobId, handlers) {
    var failures = 0;
    (function tick() {
      fetch("/jobs/" + jobId, { headers: { Accept: "application/json" } })
        .then(function (res) {
          if (!res.ok) throw new Error("status " + res.status);
          return res.json();
        })
        .then(function (job) {
          failures = 0;
          render(block, job);
          if (job.status === "running") return setTimeout(tick, POLL_MS);
          if (job.status === "done") return handlers.done(job);
          handlers.failed(job.error || "The agent stopped unexpectedly.");
        })
        .catch(function () {
          failures += 1;
          if (failures >= MAX_POLL_FAILURES) return handlers.failed("Lost contact with the server. Refresh the page to check.");
          setTimeout(tick, POLL_MS * 2);
        });
    })();
  }

  /* params: a FormData or plain object of form fields. handlers: {done(job), failed(message)}. */
  function start(block, url, params, handlers) {
    block.hidden = false;
    block.querySelector(".progress-steps").replaceChildren();
    block.querySelector(".progress-thinking").hidden = true;
    block.querySelector(".progress-error").hidden = true;
    fetch(url, { method: "POST", body: new URLSearchParams(params), headers: { Accept: "application/json" } })
      .then(function (res) {
        return res.json().then(function (body) {
          if (!res.ok) throw new Error(body.detail || "Could not start.");
          return body;
        });
      })
      .then(function (body) { poll(block, body.job_id, handlers); })
      .catch(function (err) { handlers.failed(err.message); });
  }

  function fail(block, message) {
    block.querySelector(".progress-thinking").hidden = true;
    var box = block.querySelector(".progress-error");
    box.textContent = message;
    box.hidden = false;
  }

  /* Chat: show the person's message straight away, then the assistant's real
     steps, then reload to the saved thread (same page the blocking form ends on). */
  function bindChat(form) {
    form.addEventListener("submit", function (e) {
      var box = form.querySelector("textarea[name=message]");
      var message = box.value.trim();
      if (!message) return;
      e.preventDefault();

      var card = form.parentElement;
      var block = card.querySelector(".progress");
      var thread = card.querySelector(".chat-thread");
      if (!thread) {
        thread = document.createElement("div");
        thread.className = "chat-thread";
        var empty = card.querySelector(".empty");
        if (empty) empty.replaceWith(thread);
        else card.insertBefore(thread, block);
      }
      var bubble = document.createElement("div");
      bubble.className = "chat-msg chat-msg-user";
      var who = document.createElement("span");
      who.className = "chat-msg-role";
      who.textContent = "You";
      var text = document.createElement("p");
      text.textContent = message;
      bubble.append(who, text);
      thread.appendChild(bubble);

      var controls = form.querySelectorAll("textarea, button");
      controls.forEach(function (c) { c.disabled = true; });
      var target = form.querySelector("input[name=redirect_to]").value;
      start(block, "/chat/start", { message: message }, {
        done: function () { window.location.href = target; },
        failed: function (msg) {
          fail(block, msg);
          controls.forEach(function (c) { c.disabled = false; });
        },
      });
      block.scrollIntoView({ block: "nearest" });
    });
  }

  document.querySelectorAll(".chat-form").forEach(bindChat);
  window.OrbitProgress = { start: start, fail: fail };
})();
