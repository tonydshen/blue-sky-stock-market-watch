<?php $title = "Blue Sky Marketplace Join"; 
// bsmarket-join.php
// revised on 02/09/26
// revised on 02/10/26
//  - with list_contacts improvement
// revised on 02/11/26
//  - added Unsubscribe in list_contacts, handler to be developed
// revised on 02/12/26
//  - formatted totalRows in DIV5 - listing contacts

ini_set('display_errors', 1);
ini_set('display_startup_errors', 1);
error_reporting(E_ALL);

session_start();

// Variables
$ROOT = $_SERVER['DOCUMENT_ROOT'];

// ------------------------------------------------------------
// DB connection
// ------------------------------------------------------------
require_once $ROOT . "/includes/dbclass.php"; // gives db class pdo

// ------------------------------------------------------------
// Functions
// ------------------------------------------------------------
require_once $ROOT . "/functions/functions.php"; // gives functions

// Forms
$sformfilepath = $ROOT . "/forms/simple-form.htm";
$sform = file_get_contents($sformfilepath);

$dformfilepath = $ROOT . "/forms/detail-form.htm";
$dform = file_get_contents($dformfilepath);

// ------------------------------------------------------------
// Session init
// ------------------------------------------------------------
if (!isset($_SESSION['uploads'])) {
    $_SESSION['uploads'] = [];
}

$show_simple   = true;
$show_detailed = false;
$show_upload   = false;
$show_done     = false;
$done          = false;

$fname   = "";
$email   = "";
$message = "";

$action = $_POST['action'] ?? $_GET['action'] ?? '';
// $action = $_POST['action'] ?? '';

// ------------------------------------------------------------
// 2. ACTION DISPATCHER (switch-case)
// ------------------------------------------------------------
switch ($action) {

  // ------------------------------------------------------------
  // ACTION: LOOKUP (simple form submitted)
  // ------------------------------------------------------------
  case 'lookup': 
  
      $fname = trim($_POST['fname'] ?? "");
      $email = trim($_POST['email'] ?? "");
  
      if ($email !== "") {
          $stmt = DB::$pdo->prepare("SELECT contact_id, fname FROM contact WHERE email = ?");
          $stmt->execute([$email]);
          $row = $stmt->fetch(PDO::FETCH_ASSOC);
  
          if ($row) {
              // Visitor exists → show upload interface
              $_SESSION['contact_id'] = $row['contact_id'];
              $fname = $row['fname'] ?: $fname;
  
              $show_simple = false;
              $show_upload = true;
          } else {
              // Visitor not found → show detailed form
              $show_simple   = false;
              $show_detailed = true;
          }
      }
      break; 


  // ---------------------------------------------------------------
  // ACTION: DETAIL FORM SUBMIT
  // ---------------------------------------------------------------
  case 'detail_submit': 

    $sql = "
        INSERT INTO contact (
            fname, mname, lname, title, company,
            address1, address2, address3,
            state_or_province, zip_code, country,
            web1, email, phone, fax,
            industry, sector, specialty, remark
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ";

    $stmt = DB::$pdo->prepare($sql);

    $fname    = trim($_POST['fname'] ?? '');
    $mname    = $_POST['mname'] ?? '';
    $lname    = trim($_POST['lname'] ?? '');
    $title    = $_POST['title'] ?? '';
    $company  = $_POST['company'] ?? '';
    $address1 = $_POST['address1'] ?? '';
    $address2 = $_POST['address2'] ?? '';
    $address3 = $_POST['address3'] ?? '';
    $state    = $_POST['state_or_province'] ?? '';
    $zip      = $_POST['zip_code'] ?? '';
    $country  = $_POST['country'] ?? '';
    $web1     = $_POST['web1'] ?? '';
    $email    = trim($_POST['email'] ?? '');
    $phone    = $_POST['phone'] ?? '';
    $fax      = $_POST['fax'] ?? '';
    $industry = $_POST['industry'] ?? '';
    $sector   = $_POST['sector'] ?? '';
    $specialty= $_POST['specialty'] ?? '';
    $remark   = $_POST['remark'] ?? '';

    $ok = $stmt->execute([
      $fname, $mname, $lname, $title, $company,
      $address1, $address2, $address3,
      $state, $zip, $country,
      $web1, $email, $phone, $fax,
      $industry, $sector, $specialty, $remark
    ]);

    if (!$ok) {
        // simple error handling; you can improve later
        $message = "DB execute error: " . implode(" | ", $stmt->errorInfo());
    }

    // Set $_SESSION['contact_id']
    $_SESSION['contact_id'] = DB::$pdo->lastInsertId();

    // Just show upload UI; do NOT force $action = 'upload'
    $show_simple   = false;
    $show_detailed = false;
    $show_upload   = true;
    break; 


  // ------------------------------------------------------------
  // ACTION: UPLOAD (AJAX ONLY)
  // ------------------------------------------------------------
  case 'upload':

    // If not AJAX, do not process upload; just show upload UI
    if (!is_ajax()) {
        $show_simple   = false;
        $show_detailed = false;
        $show_upload   = true;
        break;
    }

    $email      = trim($_POST['email'] ?? "");
    $contact_id = $_SESSION['contact_id'] ?? null;

    $targetDir = "/var/www/html/tmp/php/uploads/";
    if (!is_dir($targetDir)) mkdir($targetDir, 0755, true);

    $ok          = false;
    $error       = "";
    $newFileName = "";

    if (!$contact_id) {
        $error = "Missing contact_id in session.";
    } elseif (!isset($_FILES['file']) || $_FILES['file']['error'] !== UPLOAD_ERR_OK) {
        $error = "No file uploaded or upload error.";
    } else {
        $fileTmpPath = $_FILES['file']['tmp_name'];
        $fileName    = basename($_FILES['file']['name']);
        $fileSize    = $_FILES['file']['size'];

        $safeName = preg_replace("/[^a-zA-Z0-9\._-]/", "_", $fileName);
        $fileType = mime_content_type($fileTmpPath);

        $allowedTypes = [
            'image/jpeg', 'image/jpg', 'image/pjpeg',
            'image/png', 'image/x-png',
            'application/pdf', 'application/x-pdf'
        ];

        if (!in_array($fileType, $allowedTypes)) {
            $error = "Invalid file type. Allowed: JPG, PNG, PDF.";
        } elseif ($fileSize > 6 * 1024 * 1024) {
            $error = "File too large. Max size is 6MB.";
        } else {
            $newFileName = uniqid("file_", true) . "_" . $safeName;
            $destPath    = $targetDir . $newFileName;

            if (move_uploaded_file($fileTmpPath, $destPath)) {
                // Log in session
                $_SESSION['uploads'][] = $newFileName;

                // Log in DB
                $stmt = DB::$pdo->prepare("
                    INSERT INTO upload_log (contact_id, filename)
                    VALUES (?, ?)
                ");
                $stmt->execute([$contact_id, $newFileName]);

                $ok = true;
            } else {
                $error = "Failed to move uploaded file.";
            }
        }
    }

    // Pure JSON response for AJAX
    ob_clean();
    header('Content-Type: application/json');
    echo json_encode([
        'ok'      => $ok,
        'error'   => $error,
        'file'    => $newFileName,
        'uploads' => $_SESSION['uploads'],
    ]);
    exit; 


  // ------------------------------------------------------------
  // ACTION: DELETE (AJAX)
  // ------------------------------------------------------------
  case 'delete':

    $contact_id = $_SESSION['contact_id'] ?? null;
    $filename   = $_POST['filename'] ?? "";
    $ok         = false;
    $error      = "";

    if ($contact_id && $filename) {
        $targetDir = "/var/www/html/tmp/php/uploads/";
        $path      = $targetDir . $filename;

        // Remove from disk
        if (file_exists($path)) {
            @unlink($path);
        }

        // Remove from session
        $_SESSION['uploads'] = array_values(array_filter(
            $_SESSION['uploads'],
            fn($f) => $f !== $filename
        ));

        // Remove from DB
        $stmt = DB::$pdo->prepare("
            DELETE FROM upload_log
            WHERE contact_id = ? AND filename = ?
        ");
        $stmt->execute([$contact_id, $filename]);

        $ok = true;
    } else {
        $error = "Missing contact_id or filename.";
    }

    if (is_ajax()) {
        ob_clean();
        header('Content-Type: application/json');
        echo json_encode([
            'ok'      => $ok,
            'error'   => $error,
            'uploads' => $_SESSION['uploads'],
        ]);
        exit;
    }
    break; 


  // ------------------------------------------------------------
  // ACTION: DONE
  // ------------------------------------------------------------
  case 'done':

    $done          = true;
    $show_simple   = false;
    $show_detailed = false;
    $show_upload   = false;
    $show_done     = true;

    $_SESSION['uploads'] = [];
    unset($_SESSION['contact_id']);
    break; 


  // ------------------------------------------------------------
  // ACTION: LIST CONTACTS
  // ------------------------------------------------------------
  case 'list_contacts':

    // Allowed sort fields
    $allowedSort = ["fname", "lname", "title", "company", "country", "email", "phone"];
    $sort = $_GET['sort'] ?? 'lname';
    if (!in_array($sort, $allowedSort)) {
        $sort = 'lname';
    }
 
    // Sorting direction
    $direction = $_GET['direction'] ?? 'asc';
    $direction = strtolower($direction) === 'desc' ? 'desc' : 'asc';
    $arrow = ($direction === 'asc') ? '▲' : '▼';

    // Pagination
    $page  = isset($_GET['page'])  && intval($_GET['page'])  > 0 ? intval($_GET['page'])  : 1;
    $limit = isset($_GET['limit']) && intval($_GET['limit']) > 0 ? intval($_GET['limit']) : 20;
    $offset = ($page - 1) * $limit;

    // Query DB
    $stmt = DB::$pdo->prepare("
        SELECT contact_id, fname, lname, title, company, country, email, phone
        FROM contact
        ORDER BY $sort $direction
        LIMIT ? OFFSET ?
    ");
    $stmt->execute([$limit, $offset]);
    $rows = $stmt->fetchAll(PDO::FETCH_ASSOC);

    // Total count for pagination display
    $stmtCount = DB::$pdo->query("SELECT COUNT(*) FROM contact");
    $totalRows = (int)$stmtCount->fetchColumn();
    $start = $offset + 1;
    $end   = min($offset + $limit, $totalRows);

    // Build table rows
    $tableRows = "";
    foreach ($rows as $r) {
        $tableRows .= "
            <tr>
                <td>{$r['fname']}</td>
                <td>{$r['lname']}</td>
                <td>{$r['title']}</td>
                <td>{$r['company']}</td>
                <td>{$r['country']}</td>
                <td>{$r['email']}</td>
                <td>{$r['phone']}</td>
            </tr>
        ";
    }

    $nextDirection = ($direction === 'asc') ? 'desc' : 'asc';

    // Tell renderer to show the list UI
    $show_simple   = false;
    $show_detailed = false;
    $show_upload   = false;
    $show_done     = false;
    $show_list     = true;

    break;

  // --------------------------------------------------------
  // DEFAULT (no action)
  // --------------------------------------------------------
  default:
    $show_simple = true;
    break;
}

// ------------------------------------------------------------
// 3. RENDER HTML AFTER ALL HANDLERS
// ------------------------------------------------------------
include $ROOT . "/css/header.css";
include $ROOT . "/css/navbar.css";
?>

<!DOCTYPE html>
<html>
<body>
<h2 align="center">Blue Sky Marketplace</h2>

<!-- DIV 1 — Simple Contact Form -->
<div id="div1" align="center" class="section <?php echo $show_simple ? 'visible' : ''; ?>">
    <h3>Please enter your name and email and see if you are in our system</h3>
    <?php echo $sform; ?>
    <script>
    document.addEventListener("DOMContentLoaded", function () {
        const form = document.querySelector("form");   // your simple form
        const radios = form.querySelectorAll("input[type=radio][name=action]");
    
        radios.forEach(r => {
            r.addEventListener("change", () => {
                form.submit();
            });
        });
    });
    </script>

</div>

<!-- DIV 2 — Detailed Contact Form -->
<div id="div2" align="center" class="section <?php echo $show_detailed ? 'visible' : ''; ?>">
    <h3>Step 2: Provide Detailed Contact Information</h3>
    <p>We could not find your email in our system.</p>
    <?php echo $dform; ?>
</div>

<!-- DIV 3 — Upload Interface -->
<div id="div3" align="center" class="section <?php echo $show_upload ? 'visible' : ''; ?>">
    <h3>Your are in our system. You can upload files about your products and services</h3>

    <?php if ($message): ?>
        <div class="msg"><?php echo htmlspecialchars($message); ?></div>
    <?php endif; ?>

    <!-- Uploaded files list -->
    <div id="uploaded-list">
        <?php if (!empty($_SESSION['uploads'])): ?>
            <h4>Uploaded Files:</h4>
            <?php foreach ($_SESSION['uploads'] as $f): ?>
                <?php
                    $webPath = "/tmp/php/uploads/" . $f;
                    $ext = strtolower(pathinfo($f, PATHINFO_EXTENSION));
                ?>
                <div class="thumb" data-filename="<?php echo htmlspecialchars($f); ?>">
                    <?php if (in_array($ext, ['jpg','jpeg','png'])): ?>
                        <img src="<?php echo htmlspecialchars($webPath); ?>" width="80">
                    <?php else: ?>
                        <strong>[PDF]</strong>
                    <?php endif; ?>
                    <span><?php echo htmlspecialchars($f); ?></span>
                    <button type="button" class="delete-btn">Delete</button>
                </div>
            <?php endforeach; ?>
        <?php endif; ?>
    </div>

    <!-- Progress bar -->
    <div class="progress-container">
        <div id="progress-bar" class="progress-bar"></div>
    </div>
    <div id="upload-msg" class="msg"></div>

    <!-- AJAX and normal upload form -->
    <form id="upload-form" enctype="multipart/form-data" method="POST">
        <input type="hidden" name="action" id="action-field" value="">
        <input type="file" name="file" id="file-input" required><br><br>
        <button type="submit" onclick="setAction('upload')">Upload File</button>
        <button type="submit" onclick="setAction('done')" formnovalidate>Done</button>
        <button type="submit" onclick="setAction('skip')" formnovalidate>Skip</button>
        <button type="submit" onclick="setAction('list_contacts')" formnovalidate>See who else in Marketplace</button>
    </form>

    <script>
    function setAction(val) {
        document.getElementById('action-field').value = val;
    }
    </script>

</div>

<!-- DIV 5 — List Contacts -->
<div id="div5" align="center" class="section <?php echo !empty($show_list) ? 'visible' : ''; ?>">
    <p>
        Showing <?php echo $start; ?>–<?php echo $end; ?>
        of <?php echo number_format($totalRows); ?> contacts
    </p>

    <!-- Table -->
    <table border="1" cellpadding="5" cellspacing="0">
         <tr>
             <th class="<?php echo ($sort=='fname')?'active-sort':''; ?>">
                 <a href="?action=list_contacts&sort=fname&direction=<?php echo ($sort=='fname' ? $nextDirection : 'asc'); ?>&page=1&limit=<?php echo $limit; ?>">
                     First
                 </a>
                 <?php if ($sort=='fname') echo " $arrow"; ?>
             </th>
         
             <th class="<?php echo ($sort=='lname')?'active-sort':''; ?>">
                 <a href="?action=list_contacts&sort=lname&direction=<?php echo ($sort=='lname' ? $nextDirection : 'asc'); ?>&page=1&limit=<?php echo $limit; ?>">
                     Last
                 </a>
                 <?php if ($sort=='lname') echo " $arrow"; ?>
             </th>
         
             <th class="<?php echo ($sort=='title')?'active-sort':''; ?>">
                 <a href="?action=list_contacts&sort=title&direction=<?php echo ($sort=='title' ? $nextDirection : 'asc'); ?>&page=1&limit=<?php echo $limit; ?>">
                     Title
                 </a>
                 <?php if ($sort=='title') echo " $arrow"; ?>
             </th>
         
             <th class="<?php echo ($sort=='company')?'active-sort':''; ?>">
                 <a href="?action=list_contacts&sort=company&direction=<?php echo ($sort=='company' ? $nextDirection : 'asc'); ?>&page=1&limit=<?php echo $limit; ?>">
                     Company
                 </a>
                 <?php if ($sort=='company') echo " $arrow"; ?>
             </th>
         
             <th class="<?php echo ($sort=='country')?'active-sort':''; ?>">
                 <a href="?action=list_contacts&sort=country&direction=<?php echo ($sort=='country' ? $nextDirection : 'asc'); ?>&page=1&limit=<?php echo $limit; ?>">
                     Country
                 </a>
                 <?php if ($sort=='country') echo " $arrow"; ?>
             </th>
         
             <th class="<?php echo ($sort=='email')?'active-sort':''; ?>">
                 <a href="?action=list_contacts&sort=email&direction=<?php echo ($sort=='email' ? $nextDirection : 'asc'); ?>&page=1&limit=<?php echo $limit; ?>">
                     Email
                 </a>
                 <?php if ($sort=='email') echo " $arrow"; ?>
             </th>
         
             <th class="<?php echo ($sort=='phone')?'active-sort':''; ?>">
                 <a href="?action=list_contacts&sort=phone&direction=<?php echo ($sort=='phone' ? $nextDirection : 'asc'); ?>&page=1&limit=<?php echo $limit; ?>">
                     Phone
                 </a>
                 <?php if ($sort=='phone') echo " $arrow"; ?>
             </th>
         </tr>
        <?php echo $tableRows; ?>
    </table>

    <!-- Pagination -->
    <div style="margin-top: 12px;">

        <a href="bsmarket.php">What Marketplace Is</a>
        <a href="bsmarket-join.php">Join Marketplace</a>
        <a href="bsmarket-join.php">Unsubscribe</a>

        <?php if ($page > 1): ?>
            <a href="?action=list_contacts&page=1&limit=<?php echo $limit; ?>&sort=<?php echo $sort; ?>&direction=<?php echo $direction; ?>">First</a>
        <?php endif; ?>
         
        <?php if ($page > 1): ?>
            <a href="?action=list_contacts&page=<?php echo $page-1; ?>&limit=<?php echo $limit; ?>&sort=<?php echo $sort; ?>&direction=<?php echo $direction; ?>">Previous</a>
        <?php endif; ?>
         
        <?php if ($end < $totalRows): ?>
            <a href="?action=list_contacts&page=<?php echo $page+1; ?>&limit=<?php echo $limit; ?>&sort=<?php echo $sort; ?>&direction=<?php echo $direction; ?>">Next</a>
            <a href="?action=list_contacts&page=<?php echo ceil($totalRows/$limit); ?>&limit=<?php echo $limit; ?>&sort=<?php echo $sort; ?>&direction=<?php echo $direction; ?>">Last</a>
        <?php endif; ?>

    </div>

</div>

<!-- DIV 4 — Post-Upload Interface -->
<div id="div4" align="center" class="section <?php echo $show_done ? 'visible' : ''; ?>">
<?php if ($done): ?>
    <div class="section visible">
        <h3>Thank you!</h3>
        <p>Your files have been processed.</p>
        <a href=""><button>Start Over</button></a>
    </div>
<?php endif; ?>
</div>

<script>
// AJAX upload
const uploadForm   = document.getElementById('upload-form');
const fileInput    = document.getElementById('file-input');
const progressBar  = document.getElementById('progress-bar');
const uploadMsg    = document.getElementById('upload-msg');
const uploadedList = document.getElementById('uploaded-list');

if (uploadForm) {
    uploadForm.addEventListener('submit', function(e) {

    const action = document.getElementById('action-field').value;

    // Only intercept UPLOAD
    if (action !== 'upload') {
        return; // allow normal POST for done/skip
    }

    e.preventDefault(); // block only upload



        if (!fileInput.files.length) return;

        const formData = new FormData(uploadForm);
        const xhr = new XMLHttpRequest();
        xhr.open('POST', '', true);
        xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');

        xhr.upload.onprogress = function(e) {
            if (e.lengthComputable) {
                const percent = Math.round((e.loaded / e.total) * 100);
                progressBar.style.width = percent + '%';
            }
        };

        xhr.onload = function() {
            progressBar.style.width = '0%';
            if (xhr.status === 200) {
                try {
                    const res = JSON.parse(xhr.responseText);
                    if (res.ok) {
                        uploadMsg.textContent = 'Upload successful: ' + res.file;
                        uploadMsg.style.color = '#080';
                        refreshUploadedList(res.uploads);
                        fileInput.value = '';
                    } else {
                        uploadMsg.textContent = res.error || 'Upload failed.';
                        uploadMsg.style.color = '#d00';
                    }
                } catch (err) {
                    uploadMsg.textContent = 'Unexpected response.';
                    uploadMsg.style.color = '#d00';
                }
            } else {
                uploadMsg.textContent = 'Server error.';
                uploadMsg.style.color = '#d00';
            }
        };

        xhr.send(formData);
    });
}

// Refresh uploaded list (client-side render)
function refreshUploadedList(files) {
    uploadedList.innerHTML = '';
    if (!files || !files.length) return;

    const title = document.createElement('h4');
    title.textContent = 'Uploaded Files:';
    uploadedList.appendChild(title);

    files.forEach(f => {
        const ext = f.split('.').pop().toLowerCase();
        const webPath = '/tmp/php/uploads/' + encodeURIComponent(f);

        const div = document.createElement('div');
        div.className = 'thumb';
        div.dataset.filename = f;

        if (['jpg','jpeg','png'].includes(ext)) {
            const img = document.createElement('img');
            img.src = webPath;
            img.width = 80;
            div.appendChild(img);
        } else {
            const label = document.createElement('strong');
            label.textContent = '[PDF]';
            div.appendChild(label);
        }

        const span = document.createElement('span');
        span.textContent = ' ' + f;
        div.appendChild(span);

        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'delete-btn';
        btn.textContent = 'Delete';
        btn.addEventListener('click', () => deleteFile(f));
        div.appendChild(btn);

        uploadedList.appendChild(div);
    });
}

// Delete file via AJAX
function deleteFile(filename) {
    const formData = new FormData();
    formData.append('action', 'delete');
    formData.append('filename', filename);

    const xhr = new XMLHttpRequest();
    xhr.open('POST', '', true);
    xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');

    xhr.onload = function() {
        if (xhr.status === 200) {
            try {
                const res = JSON.parse(xhr.responseText);
                if (res.ok) {
                    refreshUploadedList(res.uploads);
                } else {
                    alert(res.error || 'Delete failed.');
                }
            } catch (err) {
                alert('Unexpected response.');
            }
        } else {
            alert('Server error.');
        }
    };

    xhr.send(formData);
}

// Attach delete handlers for initial PHP-rendered list
document.querySelectorAll('.delete-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        const parent = btn.closest('.thumb');
        const filename = parent.dataset.filename;
        deleteFile(filename);
    });
});
</script>
<?php include $ROOT . "/css/footer.css"; ?>
</body>
</html>

