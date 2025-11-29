# AWS Setup Guide for Plex Alexa Music Player Skill

This guide provides step-by-step instructions for setting up all AWS components required for the Plex Alexa Music Player Skill.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Step 1: Create DynamoDB Table](#step-1-create-dynamodb-table)
3. [Step 2: Create IAM Role for Lambda](#step-2-create-iam-role-for-lambda)
4. [Step 3: Create AWS Lambda Function](#step-3-create-aws-lambda-function)
5. [Step 4: Create Alexa Skill](#step-4-create-alexa-skill)
6. [Step 5: Configure Lambda Trigger](#step-5-configure-lambda-trigger)
7. [Step 6: Test Your Skill](#step-6-test-your-skill)
8. [Troubleshooting](#troubleshooting)

---

## Prerequisites

Before you begin, ensure you have:

- An AWS account with appropriate permissions
- An Amazon Developer account (for Alexa Skills)
- A Plex server with music library
- Your Plex authentication token ([How to find it](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/))
- Your exact Plex server name (as it appears in your Plex account)

---

## Step 1: Create DynamoDB Table

The DynamoDB table stores your music queue and playback state across sessions.

### Instructions:

1. **Navigate to DynamoDB**
   - Open the [AWS Console](https://console.aws.amazon.com/)
   - Search for "DynamoDB" in the services search bar
   - Click on "DynamoDB"

2. **Create Table**
   - Click the **"Create table"** button
   - Configure the following settings:

   | Setting | Value |
   |---------|-------|
   | **Table name** | `PlexAlexaQueue` |
   | **Partition key** | `user_id` (String) |
   | **Sort key** | Leave empty (none) |
   | **Table settings** | Use default settings |
   | **Read/write capacity** | On-demand (recommended) or Provisioned (1 RCU, 1 WCU minimum) |

3. **Create the Table**
   - Click **"Create table"** at the bottom
   - Wait for the table status to show "Active" (this may take 1-2 minutes)

4. **Note the Table Details**
   - Once created, note the **Table name**: `PlexAlexaQueue`
   - You'll need this for the Lambda function

### Table Schema

The table will automatically store items with this structure:
```json
{
  "user_id": "string",
  "tracks": [
    {
      "key": 12345,
      "title": "Song Title",
      "artist": "Artist Name"
    }
  ],
  "current_index": 0,
  "shuffle": false
}
```

---

## Step 2: Create IAM Role for Lambda

Your Lambda function needs permissions to access DynamoDB and write logs.

### Instructions:

1. **Navigate to IAM**
   - In the AWS Console, search for "IAM"
   - Click on "Roles" in the left sidebar
   - Click **"Create role"**

2. **Select Trusted Entity**
   - Choose **"AWS service"**
   - Select **"Lambda"** from the use case list
   - Click **"Next"**

3. **Add Permissions**
   - Search for and select these policies:
     - `AWSLambdaBasicExecutionRole` (for CloudWatch logs)
     - `AmazonDynamoDBFullAccess` (for DynamoDB access)
   - Click **"Next"**

4. **Name the Role**
   - Role name: `PlexAlexaLambdaRole`
   - Description: `Allows Lambda to access DynamoDB and CloudWatch for Plex Alexa skill`
   - Click **"Create role"**

5. **Note the Role ARN**
   - After creation, click on the role name
   - Copy the **Role ARN** (you'll need this for Lambda creation)
   - Example: `arn:aws:iam::123456789012:role/PlexAlexaLambdaRole`

---

## Step 3: Create AWS Lambda Function

The Lambda function contains the core logic for the Alexa skill.

### Part A: Prepare the Deployment Package

1. **Clone or Download This Repository**
   ```bash
   git clone <repository-url>
   cd alexa-plex-music-player-skill
   ```

2. **Configure Your Plex Credentials**
   - Open `lambda/lambda_function.py`
   - Find lines 20-21 and replace with your values:
     ```python
     PLEX_TOKEN = "YOUR_PLEX_TOKEN"  # Replace with your actual token
     PLEX_SERVER_NAME = "YOUR_SERVER_NAME"  # Replace with your exact server name
     ```
   - Save the file

3. **Install Dependencies**
   ```bash
   cd lambda/
   pip install -r requirements.txt -t ./
   ```

   This installs:
   - `plexapi` - Plex API client
   - `ask-sdk-core` - Alexa Skills Kit SDK
   - `boto3` - AWS SDK (for DynamoDB)

4. **Create Deployment Package**
   ```bash
   # Make sure you're in the lambda/ directory
   zip -r ../lambda-deployment.zip ./*
   cd ..
   ```

   This creates `lambda-deployment.zip` containing your code and all dependencies.

### Part B: Create the Lambda Function

1. **Navigate to Lambda**
   - Open the [AWS Lambda Console](https://console.aws.amazon.com/lambda/)
   - Click **"Create function"**

2. **Configure Function**
   - Select **"Author from scratch"**
   - Function name: `PlexAlexaMusicSkill`
   - Runtime: **Python 3.12** (or latest Python 3.x available)
   - Architecture: **x86_64**
   - Permissions:
     - Expand "Change default execution role"
     - Select **"Use an existing role"**
     - Choose `PlexAlexaLambdaRole` (created in Step 2)
   - Click **"Create function"**

3. **Upload Your Code**
   - In the "Code" tab, click on **"Upload from"** → **".zip file"**
   - Click **"Upload"** and select `lambda-deployment.zip`
   - Click **"Save"**

4. **Configure Function Settings**

   **Timeout:**
   - Go to "Configuration" → "General configuration"
   - Click **"Edit"**
   - Set **Timeout** to `30 seconds` (music library operations can take time)
   - Click **"Save"**

   **Memory:**
   - While in "General configuration"
   - Set **Memory** to `256 MB` (or higher for better performance)
   - Click **"Save"**

   **Environment Variables (Optional):**
   - Go to "Configuration" → "Environment variables"
   - If you prefer not to hardcode credentials, add:
     - `PLEX_TOKEN` = your token
     - `PLEX_SERVER_NAME` = your server name
     - `DYNAMODB_TABLE_NAME` = `PlexAlexaQueue`
   - Click **"Save"**

5. **Copy the Lambda ARN**
   - At the top right of the Lambda function page, copy the **Function ARN**
   - Example: `arn:aws:lambda:us-east-1:123456789012:function:PlexAlexaMusicSkill`
   - **Save this ARN** - you'll need it for the Alexa skill configuration

---

## Step 4: Create Alexa Skill

### Part A: Create the Skill

1. **Navigate to Alexa Developer Console**
   - Go to [https://developer.amazon.com/alexa/console/ask](https://developer.amazon.com/alexa/console/ask)
   - Sign in with your Amazon Developer account
   - Click **"Create Skill"**

2. **Skill Basics**
   - Skill name: `Plex Music` (or any name you prefer)
   - Primary locale: **English (US)** (or your preferred language)
   - Choose a model: **Custom**
   - Choose hosting: **Provision your own**
   - Click **"Next"**

3. **Choose Template**
   - Select **"Start from Scratch"**
   - Click **"Next"**

4. **Review and Create**
   - Review your selections
   - Click **"Create skill"**
   - Wait for the skill to be created

### Part B: Configure Invocation Name

1. **Set Invocation Name**
   - In the left sidebar, click **"Invocations"** → **"Skill Invocation Name"**
   - Enter: `plex music server` (or customize as desired)
   - This is what you'll say to activate the skill: *"Alexa, open plex music server"*
   - Click **"Save Model"**

### Part C: Configure Interaction Model

1. **Navigate to JSON Editor**
   - In the left sidebar, click **"Interaction Model"** → **"JSON Editor"**

2. **Replace the JSON**
   - Delete all existing JSON in the editor
   - Copy the entire contents from `alexa_skill/intent_model.json` in this repository
   - Paste into the JSON Editor

3. **Customize Intent Model (Optional)**
   - The `CUSTOM_ARTIST`, `CUSTOM_ALBUM`, `CUSTOM_TRACK`, and `CUSTOM_PLAYLIST` types contain sample values
   - You can add your own artists, albums, tracks, and playlists for better voice recognition
   - Note: These are just examples - the fuzzy matching in the Lambda will handle artists not listed

4. **Save and Build**
   - Click **"Save Model"**
   - Click **"Build Model"** (this may take 1-2 minutes)
   - Wait for the build to complete successfully

### Part D: Configure Endpoint

1. **Navigate to Endpoint**
   - In the left sidebar, click **"Endpoint"**

2. **Configure AWS Lambda ARN**
   - Select **"AWS Lambda ARN"**
   - Default Region: Paste your **Lambda Function ARN** (from Step 3, Part B, step 5)
   - Example: `arn:aws:lambda:us-east-1:123456789012:function:PlexAlexaMusicSkill`
   - Leave other regions empty (unless you want multi-region support)

3. **Copy the Skill ID**
   - At the top of the page, you'll see **"Your Skill ID"**
   - Click the copy button to copy it
   - Example: `amzn1.ask.skill.12345678-1234-1234-1234-123456789012`
   - **Save this Skill ID** - you'll need it for the Lambda trigger

4. **Save Endpoints**
   - Click **"Save Endpoints"**

### Part E: Configure Interfaces (Audio Player)

1. **Navigate to Interfaces**
   - In the left sidebar, click **"Interfaces"**

2. **Enable Audio Player**
   - Toggle **"Audio Player"** to ON
   - This is required for playing audio through Alexa
   - Scroll down and click **"Save Interfaces"**

---

## Step 5: Configure Lambda Trigger

Now connect the Lambda function to the Alexa skill.

### Instructions:

1. **Return to Lambda Console**
   - Go back to your Lambda function: `PlexAlexaMusicSkill`

2. **Add Trigger**
   - Click **"Add trigger"** button
   - Select source: **Alexa Skills Kit**

3. **Configure Trigger**
   - **Skill ID verification**: Enable (recommended for security)
   - **Skill ID**: Paste the Skill ID you copied from Step 4, Part D, step 3
   - Example: `amzn1.ask.skill.12345678-1234-1234-1234-123456789012`
   - Click **"Add"**

4. **Verify Trigger**
   - You should now see "Alexa Skills Kit" in the function triggers diagram
   - The Lambda function is now connected to your Alexa skill

---

## Step 6: Test Your Skill

### Test in Alexa Developer Console

1. **Navigate to Test Tab**
   - In the Alexa Developer Console, click the **"Test"** tab at the top

2. **Enable Testing**
   - Change the dropdown from "Off" to **"Development"**

3. **Test with Voice or Text**

   **Text Input Examples:**
   - Type: `open plex music server`
   - Expected response: "Plex Music is ready. You can ask me to play music from your Plex server."

   Then try:
   - `play The Beatles`
   - `play playlist Favorites`
   - `shuffle Pink Floyd`
   - `what's playing`
   - `next`
   - `previous`
   - `rate this song 4 stars`

4. **Check Lambda Logs**
   - If there are errors, check CloudWatch Logs:
     - Go to Lambda → Monitor → View logs in CloudWatch
     - Check for connection errors, missing credentials, etc.

### Test on Physical Alexa Device

1. **Ensure Device is Linked**
   - Make sure your Alexa device is linked to the same Amazon account as your Alexa Developer account

2. **Try Voice Commands**
   - Say: *"Alexa, open plex music server"*
   - Say: *"Play The Beatles"*
   - Say: *"What's playing?"*
   - Say: *"Next"*

---

## Troubleshooting

### Lambda Can't Connect to Plex

**Symptoms:** Skill says "I couldn't connect to your Plex server"

**Solutions:**
1. Verify your `PLEX_TOKEN` is correct
2. Verify your `PLEX_SERVER_NAME` exactly matches your server name (case-sensitive)
3. Check Lambda logs in CloudWatch for detailed error messages
4. Ensure your Plex server is accessible from the internet (port forwarding or Plex Relay)

### DynamoDB Permission Errors

**Symptoms:** Errors mentioning DynamoDB access denied

**Solutions:**
1. Verify the Lambda execution role has `AmazonDynamoDBFullAccess` policy
2. Verify the table name in `lambda_function.py` line 27 is `PlexAlexaQueue`
3. Verify the DynamoDB table exists and is active

### Alexa Skill Doesn't Respond

**Symptoms:** Alexa says "There was a problem with the requested skill's response"

**Solutions:**
1. Check Lambda CloudWatch logs for errors
2. Verify the Lambda trigger is configured with the correct Skill ID
3. Verify the Alexa endpoint is configured with the correct Lambda ARN
4. Ensure "Audio Player" interface is enabled in the skill

### Music Doesn't Play on Alexa Device

**Symptoms:** Skill responds but no audio plays

**Solutions:**
1. Check if your Plex server supports external streaming
2. Verify your network allows hairpin NAT (see main README for details)
3. Try using the relay URL by ensuring `USE_LOCAL_AUDIO_URL = True` in lambda_function.py
4. Check Lambda logs for audio URL generation errors
5. Test the audio URL directly in a browser to verify it's accessible

### Build Model Fails

**Symptoms:** Alexa skill model build fails with errors

**Solutions:**
1. Ensure the JSON is valid (no syntax errors)
2. Check that all required intents are present
3. Verify invocation name meets requirements (lowercase, no special characters except spaces)

### Timeout Errors

**Symptoms:** Lambda times out when playing large playlists

**Solutions:**
1. Increase Lambda timeout (Configuration → General → Timeout → 30-60 seconds)
2. Increase Lambda memory (256 MB or higher)
3. The code already limits playlists to 150 tracks to prevent timeouts

---

## Configuration Variables Reference

### Lambda Environment Variables (lambda_function.py)

| Variable | Line | Default Value | Description |
|----------|------|---------------|-------------|
| `PLEX_TOKEN` | 20 | `"YOUR_PLEX_TOKEN"` | Your Plex authentication token |
| `PLEX_SERVER_NAME` | 21 | `"YOUR_SERVER_NAME"` | Your exact Plex server name |
| `USE_LOCAL_AUDIO_URL` | 24 | `True` | Use relay URL for better Alexa compatibility |
| `DYNAMODB_TABLE_NAME` | 27 | `"PlexAlexaQueue"` | DynamoDB table name for queue storage |

### Artist Name Mappings (Optional)

Lines 29-40 in `lambda_function.py` contain mappings for voice recognition variations:

```python
ARTIST_MAPPINGS = {
    "sugar free": "Suga Free",
    "doctor dre": "Dr. Dre",
    # Add your own mappings here
}
```

The fuzzy matching system handles most cases automatically, but you can add custom mappings for tricky artist names.

---

## Next Steps

Once everything is set up and working:

1. **Customize the intent model** with your own music library items
2. **Add artist mappings** for better voice recognition
3. **Configure additional Alexa devices** by linking them to your Amazon account
4. **Monitor usage** through AWS CloudWatch and Lambda metrics
5. **Consider setting up CloudWatch alarms** for error rates

## Additional Resources

- [Plex API Documentation](https://python-plexapi.readthedocs.io/)
- [Alexa Skills Kit Documentation](https://developer.amazon.com/en-US/docs/alexa/ask-overviews/what-is-the-alexa-skills-kit.html)
- [AWS Lambda Documentation](https://docs.aws.amazon.com/lambda/)
- [DynamoDB Documentation](https://docs.aws.amazon.com/dynamodb/)
- [Finding Your Plex Token](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/)

---

## Cost Estimate

### AWS Services (Approximate Monthly Costs for Light Usage)

- **DynamoDB**: $0 - $1 (on-demand pricing, minimal reads/writes)
- **Lambda**: $0 (free tier: 1M requests, 400,000 GB-seconds)
- **CloudWatch Logs**: $0 - $0.50 (free tier: 5 GB ingestion)

**Total estimated cost**: $0 - $2/month for personal use

The skill easily fits within AWS Free Tier limits for personal use.

---

**Need help?** Check the [main README](README.md) or open an issue on GitHub.
