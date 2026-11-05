targetScope = 'resourceGroup'
param name string
param location string
param tags object
param operatorPrincipalId string
var suffix = take(uniqueString(subscription().id, resourceGroup().id, name), 10)
resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: 'aidjango${suffix}'
  location: location
  tags: tags
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    customSubDomainName: 'aidjango${suffix}'
    allowProjectManagement: true
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}
resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: account
  name: 'django-upgrade'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: { displayName: 'Django upgrade evidence' }
  dependsOn: [decision]
}
resource luna 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: account
  name: 'django-luna'
  sku: { name: 'DataZoneStandard', capacity: 10 }
  properties: {
    model: { format: 'OpenAI', name: 'gpt-6-luna', version: '2026-09-22' }
    versionUpgradeOption: 'NoAutoUpgrade'
  }
}
resource decision 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: account
  name: 'django-decision'
  sku: { name: 'DataZoneStandard', capacity: 500 }
  properties: {
    model: { format: 'Microsoft', name: 'Microsoft-Decision-1', version: '1' }
    versionUpgradeOption: 'NoAutoUpgrade'
  }
  dependsOn: [luna]
}
resource operatorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, operatorPrincipalId, '53ca6127-db72-4b80-b1b0-d745d6d5456d')
  scope: account
  properties: {
    principalId: operatorPrincipalId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '53ca6127-db72-4b80-b1b0-d745d6d5456d')
  }
}
resource bing 'Microsoft.Bing/accounts@2025-05-01-preview' = {
  name: 'bingdjango${suffix}'
  location: 'global'
  tags: tags
  kind: 'Bing.GroundingCustomSearch'
  sku: { name: 'G1' }
  properties: {}
}
resource connection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01' = {
  parent: project
  name: 'bing-django-official'
  properties: {
    authType: 'ApiKey'
    category: 'GroundingWithBingCustomSearch'
    target: 'https://api.bing.microsoft.com/'
    credentials: { key: listKeys(bing.id, '2025-05-01-preview').key1 }
    metadata: { ApiType: 'Azure', ResourceId: bing.id, Location: 'global' }
  }
}
output accountName string = account.name
output endpoint string = 'https://${account.name}.services.ai.azure.com'
output projectEndpoint string = 'https://${account.name}.services.ai.azure.com/api/projects/${project.name}'
output projectId string = project.id
output bingConnectionId string = connection.id
output bingId string = bing.id
