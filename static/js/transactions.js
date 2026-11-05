const manualHistory = document.getElementById("manual-history");
const totalRows = manualHistory.querySelector('[name="rows-TOTAL_FORMS"]');
const manualRows = document.getElementById("manual-rows");

document.getElementById("add-row").addEventListener("click", function (event) {
    // добавляет пустую строку без отправки формы
    event.preventDefault();
    const index = Number(totalRows.value);
    if (index >= Number(manualHistory.dataset.maxRows)) {
        document.getElementById("manual-limit").textContent = "Достигнут предел ручного ввода строк";
        return;
    }
    manualRows.insertAdjacentHTML("beforeend", document.getElementById("manual-empty-row").innerHTML.replaceAll("__prefix__", index));
    totalRows.value = index + 1;
    manualRows.lastElementChild.querySelector("legend").textContent = "Операция " + (index + 1);
    manualRows.lastElementChild.querySelector("input").focus();
});
