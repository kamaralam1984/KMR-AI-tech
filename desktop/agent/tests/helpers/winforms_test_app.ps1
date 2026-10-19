# A tiny Windows Forms window used only by the live Windows backend test (the Windows twin of
# gtk_test_app.py). It prints READY when shown and EVENT lines for what happens to it.
param([int]$Seconds = 90)
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$form = New-Object System.Windows.Forms.Form
$form.Text = 'KMR Test Window'
$form.Width = 400
$form.Height = 460
$form.StartPosition = 'CenterScreen'

function Say([string]$line) { [Console]::Out.WriteLine($line); [Console]::Out.Flush() }
$script:y = 10
function Add-Label([string]$text) {
  $l = New-Object System.Windows.Forms.Label
  $l.Text = $text; $l.AutoSize = $true
  $l.Location = New-Object System.Drawing.Point(10, $script:y); $script:y += 26
  $form.Controls.Add($l)
}
function Add-Button([string]$text, [string]$event) {
  $b = New-Object System.Windows.Forms.Button
  $b.Text = $text; $b.Width = 180
  $b.Location = New-Object System.Drawing.Point(10, $script:y); $script:y += 34
  $b.Add_Click({ Say("EVENT pressed:$event") }.GetNewClosure())
  $form.Controls.Add($b)
}

Add-Label 'Welcome to the test window'
Add-Label 'Error: could not load the page'
Add-Label 'Contact jane.doe@example.com or call 9876543210'
Add-Button 'Reload' 'reload'
Add-Button 'Open settings' 'open-settings'
Add-Button '' 'unnamed'
Add-Button 'Submit order' 'submit'

$search = New-Object System.Windows.Forms.TextBox
$search.AccessibleName = 'Search'; $search.Width = 180
$search.Location = New-Object System.Drawing.Point(10, $script:y); $script:y += 30
$search.Add_TextChanged({ Say("EVENT text:search:" + $search.Text) })
$form.Controls.Add($search)

$password = New-Object System.Windows.Forms.TextBox
$password.AccessibleName = 'Password'; $password.UseSystemPasswordChar = $true; $password.Width = 180
$password.Location = New-Object System.Drawing.Point(10, $script:y); $script:y += 30
$password.Add_TextChanged({ Say("EVENT text:password") })
$form.Controls.Add($password)

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = $Seconds * 1000
$timer.Add_Tick({ $form.Close() })
$timer.Start()
$form.Add_Shown({ Say 'READY' })
[void]$form.ShowDialog()
