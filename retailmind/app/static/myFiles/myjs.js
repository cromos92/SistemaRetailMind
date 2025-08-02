 function getCookie(name) {
     var cookieValue = null;
     if (document.cookie && document.cookie !== '') {
         var cookies = document.cookie.split(';');
         for (var i = 0; i < cookies.length; i++) {
             var cookie = cookies[i].trim();
             if (cookie.substring(0, name.length + 1) === (name + '=')) {
                 cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                 break;
             }
         }
     }
     return cookieValue;
 }

 function reloadDataTable(idTable, columms, dataJson, createRowP = "", order = "") {
     $('#' + idTable).dataTable().fnClearTable();
     $('#' + idTable).dataTable().fnDestroy();
     $('#' + idTable).DataTable({
         data: dataJson,
         columns: columms,
         "createdRow": createRowP,
         order: order,




     });

 }

 function mostrarLoadingBtn(idbtn) {
     // Deshabilitar el botón para evitar clics adicionales
     $('#' + idbtn).prop('disabled', true);

     // Cambiar el texto del botón a "Cargando..."
     $('#' + idbtn).html('<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Cargando...');
 }

 function hideLoadingBtn(idbtn, textobtn, statusBtn) {
     // Habilitar nuevamente el botón
     if (statusBtn == true) {
         $('#' + idbtn).prop('disabled', false);
     } else {
         $('#' + idbtn).prop('disabled', true);
     }

     $('#' + idbtn).html(textobtn);
 }
